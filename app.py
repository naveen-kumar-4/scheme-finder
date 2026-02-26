from flask import Flask, render_template, request, jsonify, redirect, session
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
import os
import re

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")

# ================= MongoDB Connection =================
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    raise Exception("MONGO_URI environment variable not set")

client = MongoClient(MONGO_URI)
db = client["gov_schemes"]  # Must match Atlas DB name

collection = db["schemes"]
saved_collection = db["saved_schemes"]
users_collection = db["users"]

# =====================================================
# ---------------- AUTH SYSTEM ------------------------
# =====================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")

        if users_collection.find_one({"email": email}):
            return "Email already exists"

        hashed_password = generate_password_hash(password)

        users_collection.insert_one({
            "name": name,
            "email": email,
            "password": hashed_password
        })

        return redirect("/login")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = users_collection.find_one({"email": email})

        if user and check_password_hash(user["password"], password):
            session["user_id"] = str(user["_id"])
            session["user_name"] = user["name"]
            return redirect("/dashboard")

        return "Invalid credentials"

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# =====================================================
# ---------------- DASHBOARD --------------------------
# =====================================================

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/login")

    return render_template(
        "dashboard.html",
        name=session["user_name"]
    )


# =====================================================
# ---------------- HOME SEARCH ------------------------
# =====================================================

@app.route("/")
def home():
    search_query = request.args.get("search", "").strip()

    if search_query:
        words = re.split(r"\s+", search_query)
        query_conditions = []
        for word in words:
            regex = {"$regex": word, "$options": "i"}
            query_conditions.append({
                "$or": [
                    {"scheme_name": regex},
                    {"details": regex},
                    {"benefits": regex},
                    {"eligibility": regex},
                    {"schemeCategory": regex},
                    {"applicable_state": regex},
                    {"documents_required": regex},
                    {"scheme_status": regex}
                ]
            })
        all_schemes = list(collection.find({"$and": query_conditions}))
    else:
        all_schemes = list(collection.find())

    # Convert ObjectId to string
    for s in all_schemes:
        s["_id"] = str(s["_id"])

    # Filter by status
    ongoing = [s for s in all_schemes if str(s.get("scheme_status", "")).lower() == "ongoing"]
    upcoming = [s for s in all_schemes if str(s.get("scheme_status", "")).lower() == "upcoming"]
    expired = [s for s in all_schemes if str(s.get("scheme_status", "")).lower() == "expired"]

    return render_template(
        "index.html",
        ongoing_schemes=ongoing,
        upcoming_schemes=upcoming,
        expired_schemes=expired,
        search_query=search_query
    )

# =====================================================
# ---------------- VIEW SCHEME DETAILS ----------------
# =====================================================

@app.route("/scheme/<scheme_id>")
def scheme_details(scheme_id):
    try:
        scheme = collection.find_one({"_id": ObjectId(scheme_id)})
        if not scheme:
            return "Scheme not found", 404

        scheme["_id"] = str(scheme["_id"])

        # Check if saved by this user
        if "user_id" in session:
            existing = saved_collection.find_one({
                "scheme_id": scheme_id,
                "user_id": session["user_id"]
            })
            is_saved = True if existing else False
        else:
            is_saved = False

        return render_template(
            "save_scheme.html",
            scheme=scheme,
            is_saved=is_saved
        )

    except:
        return "Invalid Scheme ID", 400


# =====================================================
# ---------------- SAVE SCHEME ------------------------
# =====================================================

@app.route("/save_scheme", methods=["POST"])
def save_scheme():
    if "user_id" not in session:
        return jsonify({"status": "login_required"})

    data = request.json
    scheme_id = data.get("scheme_id")

    existing = saved_collection.find_one({
        "scheme_id": scheme_id,
        "user_id": session["user_id"]
    })

    if existing:
        return jsonify({"status": "exists"})

    saved_collection.insert_one({
        "scheme_id": scheme_id,
        "user_id": session["user_id"]
    })

    return jsonify({"status": "success"})


# =====================================================
# ---------------- VIEW SAVED SCHEMES -----------------
# =====================================================

@app.route("/saved_schemes")
def view_saved():
    if "user_id" not in session:
        return redirect("/login")

    saved_items = list(saved_collection.find({
        "user_id": session["user_id"]
    }))

    scheme_ids = [item["scheme_id"] for item in saved_items]

    if not scheme_ids:
        return render_template("saved_schemes.html", schemes=[])

    object_ids = [ObjectId(sid) for sid in scheme_ids]

    full_schemes = list(collection.find({
        "_id": {"$in": object_ids}
    }))

    for scheme in full_schemes:
        scheme["_id"] = str(scheme["_id"])

    return render_template(
        "saved_schemes.html",
        schemes=full_schemes
    )


# =====================================================
# ---------------- DELETE / UNSAVE --------------------
# =====================================================

@app.route("/delete_saved/<scheme_id>", methods=["POST"])
def delete_saved(scheme_id):
    if "user_id" not in session:
        return jsonify({"status": "login_required"})

    saved_collection.delete_one({
        "scheme_id": scheme_id,
        "user_id": session["user_id"]
    })

    return jsonify({"status": "deleted"})


# =====================================================
# ---------------- ELIGIBILITY API --------------------
# =====================================================

@app.route("/api/eligibility", methods=["POST"])
def check_eligibility():
    data = request.get_json()
    query = {}

    if data.get("level"):
        query["level"] = data.get("level")

    if data.get("state"):
        query["applicable_state"] = {
            "$regex": data.get("state"),
            "$options": "i"
        }

    matched_schemes = list(collection.find(query).limit(20))

    for scheme in matched_schemes:
        scheme["_id"] = str(scheme["_id"])

    return jsonify(matched_schemes)


# =====================================================
# ---------------- RUN SERVER ------------------------
# =====================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
