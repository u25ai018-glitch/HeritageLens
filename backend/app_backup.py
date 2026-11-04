
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from PIL import Image
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import psycopg2
import pytesseract
import os
import uuid
import secrets

load_dotenv()

app = Flask(__name__)
CORS(app)

app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

# Tesseract OCR path
pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
VISITOR_FOLDER = os.path.join(UPLOAD_FOLDER, "visitor_photos")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(VISITOR_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


# PostgreSQL connection
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )


# Check image extension
def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


# Home API
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "message": "HeritageLens Backend is running!"
    })


# OCR API
@app.route("/api/ocr", methods=["POST"])
def extract_text():

    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    file = request.files["image"]

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    try:
        image = Image.open(file.stream)
        image.verify()

        file.stream.seek(0)
        image = Image.open(file.stream).convert("RGB")

        extracted_text = pytesseract.image_to_string(
            image,
            lang="eng"
        )

        return jsonify({
            "success": True,
            "filename": file.filename,
            "extracted_text": extracted_text
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 400


# Get visitor photos - No admin approval required
@app.route("/api/visitor-photos/<int:monument_id>", methods=["GET"])
def get_visitor_photos(monument_id):

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, visitor_name, photo_path, caption, uploaded_at
            FROM visitor_photos
            WHERE monument_id = %s
            ORDER BY uploaded_at DESC
            """,
            (monument_id,)
        )

        photos = cursor.fetchall()
        result = []

        for photo in photos:
            filename = os.path.basename(photo[2])

            result.append({
                "id": photo[0],
                "visitor_name": photo[1],
                "photo_url": (
                    f"http://127.0.0.1:5000/uploads/visitor_photos/{filename}"
                ),
                "caption": photo[3],
                "uploaded_at": photo[4].isoformat() if photo[4] else None
            })

        return jsonify({
            "success": True,
            "photos": result
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# Visitor Photo Upload API - Direct gallery upload
@app.route("/api/visitor-photos", methods=["POST"])
def upload_visitor_photo():

    visitor_name = request.form.get("visitor_name", "").strip()
    monument_id = request.form.get("monument_id", "").strip()
    caption = request.form.get("caption", "").strip()

    if not visitor_name or not monument_id:
        return jsonify({
            "success": False,
            "error": "Visitor name and monument ID are required"
        }), 400

    if len(visitor_name) > 100 or len(caption) > 1000:
        return jsonify({
            "success": False,
            "error": "Name or caption is too long"
        }), 400

    try:
        monument_id = int(monument_id)

        if monument_id not in [1, 2, 3, 4, 5]:
            return jsonify({
                "success": False,
                "error": "Invalid monument ID"
            }), 400

    except ValueError:
        return jsonify({
            "success": False,
            "error": "Invalid monument ID"
        }), 400

    if "photo" not in request.files:
        return jsonify({
            "success": False,
            "error": "Please select a photo"
        }), 400

    photo = request.files["photo"]

    if photo.filename == "" or not allowed_file(photo.filename):
        return jsonify({
            "success": False,
            "error": "Please upload PNG, JPG, JPEG or WEBP image"
        }), 400

    filename = secure_filename(photo.filename)
    extension = filename.rsplit(".", 1)[1].lower()

    unique_filename = f"{uuid.uuid4().hex}.{extension}"
    photo_path = os.path.join(VISITOR_FOLDER, unique_filename)

    connection = None
    cursor = None

    try:
        image = Image.open(photo.stream)
        image.verify()

        photo.stream.seek(0)
        image = Image.open(photo.stream)

        if image.format not in ["PNG", "JPEG", "WEBP"]:
            return jsonify({
                "success": False,
                "error": "Invalid image format"
            }), 400

        image.save(photo_path)

        relative_path = f"uploads/visitor_photos/{unique_filename}"

        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO visitor_photos
            (monument_id, visitor_name, photo_path, caption, approval_status)
            VALUES (%s, %s, %s, %s, 'approved')
            RETURNING id
            """,
            (monument_id, visitor_name, relative_path, caption)
        )

        photo_id = cursor.fetchone()[0]

        connection.commit()

        return jsonify({
            "success": True,
            "message": "Photo added to gallery successfully!",
            "photo_id": photo_id,
            "approval_status": "approved"
        }), 201

    except Exception as error:

        if connection:
            connection.rollback()

        if os.path.exists(photo_path):
            os.remove(photo_path)

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

# Admin Delete Visitor Photo API
@app.route("/api/admin/visitor-photos/<int:photo_id>", methods=["DELETE"])
def delete_visitor_photo(photo_id):

    # Verify admin password
    admin_key = os.getenv("ADMIN_DELETE_KEY")
    provided_key = request.headers.get("X-Admin-Key", "")

    if not admin_key or not secrets.compare_digest(provided_key, admin_key):
        return jsonify({
            "success": False,
            "error": "Unauthorized: Admin password is incorrect"
        }), 401

    connection = None
    cursor = None
    photo_path = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Find photo
        cursor.execute(
            """
            SELECT photo_path
            FROM visitor_photos
            WHERE id = %s
            """,
            (photo_id,)
        )

        result = cursor.fetchone()

        if not result:
            return jsonify({
                "success": False,
                "error": "Photo not found"
            }), 404

        photo_path = result[0]

        # Delete database record
        cursor.execute(
            "DELETE FROM visitor_photos WHERE id = %s",
            (photo_id,)
        )

        connection.commit()

        # Delete image file from server
        filename = os.path.basename(photo_path)
        full_path = os.path.join(VISITOR_FOLDER, filename)

        if os.path.isfile(full_path):
            os.remove(full_path)

        return jsonify({
            "success": True,
            "message": "Photo deleted successfully!"
        }), 200

    except Exception as error:

        if connection:
            connection.rollback()

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ==========================================
# COMMUNITY SUGGESTED PLACES APIs
# ==========================================

# Visitor submits a nearby place
@app.route("/api/nearby-places", methods=["POST"])
def submit_nearby_place():

    data = request.get_json(silent=True) or {}

    visitor_name = str(data.get("visitor_name", "")).strip()
    monument_id = data.get("monument_id")
    place_name = str(data.get("place_name", "")).strip()
    location = str(data.get("location", "")).strip()
    description = str(data.get("description", "")).strip()

    if not visitor_name or not place_name or not location:
        return jsonify({
            "success": False,
            "error": "Name, place name and location are required"
        }), 400

    if (
        len(visitor_name) > 100
        or len(place_name) > 150
        or len(location) > 500
        or len(description) > 1000
    ):
        return jsonify({
            "success": False,
            "error": "One or more fields are too long"
        }), 400

    try:
        monument_id = int(monument_id)

        if monument_id not in [1, 2, 3, 4, 5]:
            raise ValueError()

    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "error": "Invalid monument ID"
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO nearby_places
            (monument_id, visitor_name, place_name, location,
             description, approval_status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
            RETURNING id
            """,
            (
                monument_id,
                visitor_name,
                place_name,
                location,
                description
            )
        )

        place_id = cursor.fetchone()[0]
        connection.commit()

        return jsonify({
            "success": True,
            "message": "Place suggestion submitted for admin approval!",
            "place_id": place_id,
            "approval_status": "pending"
        }), 201

    except Exception as error:
        if connection:
            connection.rollback()

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# Public API: Get only approved nearby places
@app.route("/api/nearby-places/<int:monument_id>", methods=["GET"])
def get_nearby_places(monument_id):

    if monument_id not in [1, 2, 3, 4, 5]:
        return jsonify({
            "success": False,
            "error": "Invalid monument ID"
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, visitor_name, place_name, location,
                   description, submitted_at
            FROM nearby_places
            WHERE monument_id = %s
            AND approval_status = 'approved'
            ORDER BY submitted_at DESC
            """,
            (monument_id,)
        )

        rows = cursor.fetchall()

        places = []

        for row in rows:
            places.append({
                "id": row[0],
                "visitor_name": row[1],
                "place_name": row[2],
                "location": row[3],
                "description": row[4],
                "submitted_at": (
                    row[5].isoformat() if row[5] else None
                )
            })

        return jsonify({
            "success": True,
            "places": places
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# Admin API: View pending suggestions
@app.route("/api/admin/nearby-places", methods=["GET"])
def admin_get_nearby_places():

    admin_key = os.getenv("ADMIN_DELETE_KEY")
    provided_key = request.headers.get("X-Admin-Key", "")

    if not admin_key or not secrets.compare_digest(
        provided_key, admin_key
    ):
        return jsonify({
            "success": False,
            "error": "Unauthorized: Admin password is incorrect"
        }), 401

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, monument_id, visitor_name, place_name,
                   location, description, submitted_at
            FROM nearby_places
            WHERE approval_status = 'pending'
            ORDER BY submitted_at DESC
            """
        )

        rows = cursor.fetchall()
        places = []

        for row in rows:
            places.append({
                "id": row[0],
                "monument_id": row[1],
                "visitor_name": row[2],
                "place_name": row[3],
                "location": row[4],
                "description": row[5],
                "submitted_at": (
                    row[6].isoformat() if row[6] else None
                )
            })

        return jsonify({
            "success": True,
            "places": places
        })

    except Exception as error:
        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# Admin API: Approve or reject a suggestion
@app.route(
    "/api/admin/nearby-places/<int:place_id>",
    methods=["PATCH"]
)
def review_nearby_place(place_id):

    admin_key = os.getenv("ADMIN_DELETE_KEY")
    provided_key = request.headers.get("X-Admin-Key", "")

    if not admin_key or not secrets.compare_digest(
        provided_key, admin_key
    ):
        return jsonify({
            "success": False,
            "error": "Unauthorized: Admin password is incorrect"
        }), 401

    data = request.get_json(silent=True) or {}
    status = data.get("status")

    if status not in ["approved", "rejected"]:
        return jsonify({
            "success": False,
            "error": "Status must be approved or rejected"
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE nearby_places
            SET approval_status = %s
            WHERE id = %s
            RETURNING id
            """,
            (status, place_id)
        )

        result = cursor.fetchone()

        if not result:
            return jsonify({
                "success": False,
                "error": "Place suggestion not found"
            }), 404

        connection.commit()

        return jsonify({
            "success": True,
            "message": f"Place suggestion {status} successfully!"
        })

    except Exception as error:
        if connection:
            connection.rollback()

        return jsonify({
            "success": False,
            "error": str(error)
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# Display uploaded visitor photos
@app.route("/uploads/visitor_photos/<filename>", methods=["GET"])
def get_visitor_photo(filename):
    return send_from_directory(VISITOR_FOLDER, filename)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
