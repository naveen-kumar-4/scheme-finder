from flask import Flask, render_template, request, jsonify, redirect, session
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
import re
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")  # change in production

# ---------------- MongoDB Connection ----------------
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    raise Exception("MONGO_URI environment variable not set")

client = MongoClient(MONGO_URI)
db = client["gov_schemes"]  # Use the DB name in Atlas
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

    user_saved = list(saved_collection.find({"user_id": session["user_id"]}))

    return render_template(
        "dashboard.html",
        name=session["user_name"],
        saved=user_saved
    )


# =====================================================
# ---------------- SMART MATCH FUNCTION ---------------
# =====================================================

def match_schemes(filters):
    age = filters.get("age")
    gender = filters.get("gender", "").lower()
    category = filters.get("category", "").lower()
    occupation = filters.get("occupation", "").lower()
    state = filters.get("state", "").lower()
    level = filters.get("level", "").lower()

    all_schemes = list(collection.find())
    matched_schemes = []

    for scheme in all_schemes:
        searchable_text = " ".join([
            str(scheme.get("scheme_name", "")),
            str(scheme.get("details", "")),
            str(scheme.get("benefits", "")),
            str(scheme.get("eligibility", "")),
            str(scheme.get("schemeCategory", "")),
            str(scheme.get("applicable_state", "")),
            str(scheme.get("level", "")),
            str(scheme.get("documents_required", "")),
            str(scheme.get("scheme_status", ""))
        ]).lower()

        score = 0
        if occupation and occupation in searchable_text:
            score += 3
        if category and category in searchable_text:
            score += 2
        if gender and gender in searchable_text:
            score += 1

        scheme_state = str(scheme.get("applicable_state", "")).lower()
        if state:
            if state in scheme_state:
                score += 3
            elif "all" in scheme_state or "central" in scheme_state:
                score += 2

        if level and level in searchable_text:
            score += 2

        if age:
            try:
                age_val = int(age)
                eligibility_text = str(scheme.get("eligibility", "")).lower()
                if str(age_val) in eligibility_text:
                    score += 2
                if "student" in eligibility_text and age_val <= 25:
                    score += 2
                if "senior" in eligibility_text and age_val >= 60:
                    score += 2
            except:
                pass

        if score > 0:
            scheme_copy = dict(scheme)
            scheme_copy["match_score"] = score
            matched_schemes.append(scheme_copy)

    matched_schemes.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return matched_schemes if matched_schemes else all_schemes


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
# ---------------- API SEARCH -------------------------
# =====================================================

@app.route("/api/search", methods=["POST"])
def api_search():
    filters = request.json
    matched = match_schemes(filters)
    for scheme in matched:
        scheme["_id"] = str(scheme["_id"])
    return jsonify(matched)


# =====================================================
# ---------------- VIEW DETAILS -----------------------
# =====================================================

@app.route("/scheme/<scheme_id>")
def scheme_details(scheme_id):
    try:
        scheme = collection.find_one({"_id": ObjectId(scheme_id)})
        if not scheme:
            return "Scheme not found", 404
        scheme["_id"] = str(scheme["_id"])
        existing = saved_collection.find_one({"scheme_id": scheme_id})
        is_saved = True if existing else False
        return render_template("save_scheme.html",
                               scheme=scheme,
                               is_saved=is_saved)
    except:
        return "Invalid Scheme ID", 400


# =====================================================
# ---------------- SAVE SCHEME ------------------------
# =====================================================

@app.route("/save_scheme", methods=["POST"])
def save_scheme():
    if "user_id" not in session:
        return {"status": "login_required"}

    data = request.json
    scheme_id = data.get("scheme_id")

    existing = saved_collection.find_one({
        "scheme_id": scheme_id,
        "user_id": session["user_id"]
    })
    if existing:
        return {"status": "exists"}

    saved_collection.insert_one({
        "scheme_id": scheme_id,
        "user_id": session["user_id"]
    })
    return {"status": "success"}


# =====================================================
# ---------------- DELETE SAVED -----------------------
# =====================================================

@app.route("/delete_saved/<scheme_id>", methods=["POST"])
def delete_saved(scheme_id):
    if "user_id" not in session:
        return {"status": "login_required"}

    saved_collection.delete_one({
        "scheme_id": scheme_id,
        "user_id": session["user_id"]
    })
    return {"status": "deleted"}


# =====================================================
# ---------------- ELIGIBILITY API --------------------
# =====================================================

@app.route("/api/eligibility", methods=["POST"])
def check_eligibility():
    data = request.get_json()
    age = data.get("age")
    gender = data.get("gender")
    category = data.get("category")
    occupation = data.get("occupation")
    level = data.get("level")
    state = data.get("state")

    query = {}

    # Level filter
    if level:
        query["level"] = level

    # State filter (only if State level selected)
    if level == "State" and state:
        query["applicable_state"] = {"$regex": state, "$options": "i"}

    # OR conditions (eligibility soft match)
    or_conditions = []
    if occupation:
        or_conditions.append({"eligibility": {"$regex": occupation, "$options": "i"}})
    if category:
        or_conditions.append({"eligibility": {"$regex": category, "$options": "i"}})
    if gender:
        or_conditions.append({"eligibility": {"$regex": gender, "$options": "i"}})
    if age:
        or_conditions.append({"eligibility": {"$regex": str(age), "$options": "i"}})

    if or_conditions:
        query["$or"] = or_conditions

    matched_schemes = list(collection.find(query).limit(20))

    # Relax filters if nothing found
    if len(matched_schemes) == 0:
        relaxed_query = {}
        if level:
            relaxed_query["level"] = level
        if level == "State" and state:
            relaxed_query["applicable_state"] = {"$regex": state, "$options": "i"}
        matched_schemes = list(collection.find(relaxed_query).limit(20))

    if len(matched_schemes) == 0:
        matched_schemes = list(collection.find().limit(10))

    for scheme in matched_schemes:
        scheme["_id"] = str(scheme["_id"])

    return jsonify(matched_schemes)


# ---------------- RUN SERVER ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
