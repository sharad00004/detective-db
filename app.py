import os
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from sqlalchemy import and_
from difflib import SequenceMatcher
from fuzzywuzzy import fuzz

load_dotenv()  # Load .env variables

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-fallback-secret')
#app.secret_key = 'detective-secret-key'  # Needed for sessions and flash messages

# Use Render PostgreSQL if available, else fallback to local SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///detectivedb.sqlite3')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# File upload config
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)

# -------------------- Database Models --------------------

class ClientUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))
    registered_on = db.Column(db.DateTime, default=datetime.utcnow)

class DetectiveUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    username = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))
    registered_on = db.Column(db.DateTime, default=datetime.utcnow)

class LostItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client_user.id'))
    title = db.Column(db.String(100))
    description = db.Column(db.Text)
    category = db.Column(db.String(50))
    location_lost = db.Column(db.String(100))
    date_lost = db.Column(db.Date)
    image_filename = db.Column(db.String(100))
    status = db.Column(db.String(20), default="pending")
    submitted_on = db.Column(db.DateTime, default=datetime.utcnow)
    matched = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)

class FoundItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client_user.id'))
    title = db.Column(db.String(100))
    description = db.Column(db.Text)
    category = db.Column(db.String(50))
    location_found = db.Column(db.String(100))
    date_found = db.Column(db.Date)
    image_filename = db.Column(db.String(100))
    status = db.Column(db.String(20), default="pending")
    submitted_on = db.Column(db.DateTime, default=datetime.utcnow)
    matched = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)

class MatchedPair(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lost_item_id = db.Column(db.Integer, db.ForeignKey('lost_item.id', ondelete='CASCADE'))
    found_item_id = db.Column(db.Integer, db.ForeignKey('found_item.id', ondelete='CASCADE'))
    status = db.Column(db.String, default='pending')
    created_on = db.Column(db.DateTime, default=datetime.utcnow)

    lost_item = db.relationship('LostItem', passive_deletes=True)
    found_item = db.relationship('FoundItem', passive_deletes=True)


class SolvedCase(db.Model):
    __tablename__ = 'solved_case'
    id = db.Column(db.Integer, primary_key=True)
    lost_item_id = db.Column(db.Integer, db.ForeignKey('lost_item.id'), nullable=False)
    found_item_id = db.Column(db.Integer, db.ForeignKey('found_item.id'), nullable=False)
    approved_on = db.Column(db.DateTime, default=datetime.utcnow)

    lost_item = db.relationship('LostItem', backref='solved_case', lazy=True)
    found_item = db.relationship('FoundItem', backref='solved_case', lazy=True)

class RejectedMatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lost_id = db.Column(db.Integer, nullable=False)
    found_id = db.Column(db.Integer, nullable=False)


# -------------------- Helper Query Functions --------------------

def get_all_pending_lost_cases():
    return LostItem.query.filter_by(status='pending').all()

def get_all_pending_found_cases():
    return FoundItem.query.filter_by(status='pending').all()

def get_solved_cases():
    return MatchedPair.query.filter_by(approved=True).all()

# 🔎 Unresolved lost item reports
def get_lost_cases():
    return LostItem.query.filter_by(is_resolved=False).all()

# 🔎 Unresolved found item reports
def get_found_cases():
    return FoundItem.query.filter_by(is_resolved=False).all()

# ✅ Matched and approved cases
def get_solved_cases():
    return MatchedCase.query.filter_by(is_approved=True).all()

def get_potential_matches():
    # Step 1: Get all unmatched and unapproved lost & found items
    lost_items = LostItem.query.filter_by(matched=False, is_approved=False).all()
    found_items = FoundItem.query.filter_by(matched=False, is_approved=False).all()

    # Step 2: Fetch rejected pairs to skip
    rejected = RejectedMatch.query.all()
    rejected_pairs = {(r.lost_id, r.found_id) for r in rejected}

    # Step 3: Find similar matches, excluding rejected ones
    matches = []
    for lost in lost_items:
        for found in found_items:
            if found.date_found >= lost.date_lost:
                similarity = fuzz.token_set_ratio(lost.description, found.description)
                if similarity >= 70 and (lost.id, found.id) not in rejected_pairs:
                    matches.append((lost, found))
    
    return matches


# -------------------- Routes --------------------

@app.route('/')
def home():
    return render_template('index.html')

# Client-side pages
@app.route('/client-home')
def client_home():
    lost_cases = get_all_pending_lost_cases()
    found_cases = get_all_pending_found_cases()
    solved_cases = SolvedCase.query.all()  # Fetch all solved cases
    return render_template('client-home.html', lost_cases=lost_cases, found_cases=found_cases, solved_cases=solved_cases)


@app.route('/client-dashboard')
def client_dashboard():
    if 'client_id' not in session:
        flash("Please login first.")
        return redirect(url_for('client_login'))
    return render_template('client-dashboard.html')

@app.route('/report-lost', methods=['GET', 'POST'])
def report_lost():
    if 'client_id' not in session:
        flash("Please login first.")
        return redirect(url_for('client_login'))

    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        category = request.form['category']
        location_lost = request.form['location_lost']
        date_lost = request.form['date_lost']
        image = request.files['image']
        image_filename = None

        if image and image.filename != '':
            filename = secure_filename(image.filename)
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_filename = filename

        lost_item = LostItem(
            client_id=session['client_id'],
            title=title,
            description=description,
            category=category,
            location_lost=location_lost,
            date_lost=date_lost,
            image_filename=image_filename
        )
        db.session.add(lost_item)
        db.session.commit()
        flash('Lost item report submitted successfully!')
        return redirect(url_for('client_dashboard'))

    return render_template('report-lost.html')


@app.route('/report-found', methods=['GET', 'POST'])
def report_found():
    if 'client_id' not in session:
        flash("Please login first.")
        return redirect(url_for('client_login'))

    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        category = request.form['category']
        location_found = request.form['location_found']
        date_found = request.form['date_found']
        image = request.files['image']
        image_filename = None

        if image and image.filename != '':
            filename = secure_filename(image.filename)
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_filename = filename

        found_item = FoundItem(
            client_id=session['client_id'],
            title=title,
            description=description,
            category=category,
            location_found=location_found,
            date_found=date_found,
            image_filename=image_filename
        )
        db.session.add(found_item)
        db.session.commit()
        flash('Found item report submitted successfully!')
        return redirect(url_for('client_dashboard'))

    return render_template('report-found.html')

from flask import request, redirect, url_for, flash, render_template
import os
from werkzeug.utils import secure_filename

# === EDIT LOST ITEM ===
@app.route('/edit-lost/<int:item_id>', methods=['GET', 'POST'])
def edit_lost(item_id):
    if 'client_id' not in session:
        return redirect(url_for('client_login'))

    item = LostItem.query.filter_by(id=item_id, client_id=session['client_id']).first()

    if not item:
        flash("Lost item not found or access denied.", "danger")
        return redirect(url_for('my_submissions'))

    if request.method == 'POST':
        item.title = request.form['title']
        item.description = request.form['description']
        item.category = request.form['category']
        item.location_lost = request.form['location_lost']
        item.date_lost = request.form['date_lost']

        image = request.files.get('image')
        if image and image.filename != '':
            filename = secure_filename(image.filename)
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            item.image_filename = filename

        db.session.commit()
        flash("Lost item updated!", "success")
        return redirect(url_for('my_submissions'))

    return render_template('edit-lost.html', item=item)



# === EDIT FOUND ITEM ===
@app.route('/edit-found/<int:item_id>', methods=['GET', 'POST'])
def edit_found(item_id):
    if 'client_id' not in session:
        return redirect(url_for('client_login'))

    item = FoundItem.query.filter_by(id=item_id, client_id=session['client_id']).first()

    if not item:
        flash("Found item not found or access denied.", "danger")
        return redirect(url_for('my_submissions'))

    if request.method == 'POST':
        item.title = request.form['title']
        item.description = request.form['description']
        item.category = request.form['category']
        item.location_found = request.form['location_found']
        item.date_found = request.form['date_found']

        image = request.files.get('image')
        if image and image.filename != '':
            filename = secure_filename(image.filename)
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            item.image_filename = filename

        db.session.commit()
        flash("Found item updated!", "success")
        return redirect(url_for('my_submissions'))

    return render_template('edit-found.html', item=item)



# === DELETE LOST ITEM ===
@app.route('/delete-lost/<int:item_id>', methods=['POST'])
def delete_lost(item_id):
    if 'client_id' not in session:
        return redirect(url_for('client_login'))

    item = LostItem.query.filter_by(id=item_id, client_id=session['client_id']).first()
    if item:
        db.session.delete(item)
        db.session.commit()
        flash("Lost report deleted.", "info")
    else:
        flash("Lost report not found or access denied.", "danger")

    return redirect(url_for('my_submissions'))



# === DELETE FOUND ITEM ===
@app.route('/delete-found/<int:item_id>', methods=['POST'])
def delete_found(item_id):
    if 'client_id' not in session:
        return redirect(url_for('client_login'))

    cur = conn.cursor()
    cur.execute("DELETE FROM found_items WHERE id = %s AND client_id = %s", (item_id, session['client_id']))
    conn.commit()
    flash("Found report deleted.", "info")
    return redirect(url_for('my_submissions'))


@app.route('/my-submissions')
def my_submissions():
    if 'client_id' not in session:
        flash("Please login first.")
        return redirect(url_for('client_login'))

    client_id = session['client_id']
    lost_items = LostItem.query.filter_by(client_id=client_id).all()
    found_items = FoundItem.query.filter_by(client_id=client_id).all()

    return render_template('my-submissions.html', lost_items=lost_items, found_items=found_items)




# Detective-side pages
@app.route('/pending-cases')
def pending_cases():
    if 'detective_id' not in session:
        flash("Please login as a detective first.")
        return redirect(url_for('detective_login'))

    pending_lost = LostItem.query.filter_by(status='pending').all()
    pending_found = FoundItem.query.filter_by(status='pending').all()

    print("LOST ITEMS:", pending_lost)
    print("FOUND ITEMS:", pending_found)

    return render_template('pending-cases.html', lost_items=pending_lost, found_items=pending_found)

@app.route('/detective-dashboard')
def detective_dashboard():
    return render_template('detective-dashboard.html')

# -------------------- Auth Routes --------------------

@app.route('/client-register', methods=['GET', 'POST'])
def client_register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']

        if ClientUser.query.filter_by(email=email).first():
            flash('Email already registered.')
            return redirect(url_for('client_register'))

        new_user = ClientUser(name=name, email=email, password=password)
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful! Please login.')
        return redirect(url_for('client_login'))

    return render_template('client-register.html')

@app.route('/client-login', methods=['GET', 'POST'])
def client_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = ClientUser.query.filter_by(email=email, password=password).first()

        if user:
            session['client_id'] = user.id  # Store in session
            flash('Login successful!')
            return redirect(url_for('client_dashboard'))
        else:
            flash('Invalid credentials.')
            return redirect(url_for('client_login'))

    return render_template('client-login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.')
    return redirect(url_for('client_login'))



@app.route('/detective-login', methods=['GET', 'POST'])
def detective_login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        detective = DetectiveUser.query.filter_by(username=email, password=password).first()

        if detective:
            session['detective_id'] = detective.id
            flash('Detective login successful!')
            return redirect(url_for('detective_dashboard'))
        else:
            flash('Invalid email or password.')
            return redirect(url_for('detective_login'))

    return render_template('detective-login.html')

@app.route('/detective-logout')
def detective_logout():
    session.clear()
    flash('Detective logged out successfully.')
    return redirect(url_for('detective_login'))


@app.route('/match-review')
def match_review():
    # Recalculate or pull current potential matches from DB
    matches = get_potential_matches()  # Your matching logic
    return render_template('match-review.html', matches=matches)


@app.route('/approve_match/<int:lost_id>/<int:found_id>', methods=['POST'])
def approve_match(lost_id, found_id):
    # Step 1: Add to MatchedPair table
    match = MatchedPair(
        lost_item_id=lost_id,
        found_item_id=found_id,
        status='approved'
    )
    db.session.add(match)
    db.session.commit()

    # Step 2: Add to SolvedCase table
    solved = SolvedCase(
        lost_item_id=lost_id,
        found_item_id=found_id
    )
    db.session.add(solved)

    # Step 3: Flag original items as approved and mark status as 'solved'
    lost_item = LostItem.query.get(lost_id)
    found_item = FoundItem.query.get(found_id)

    if lost_item:
        lost_item.is_approved = True
        lost_item.status = 'solved'
    if found_item:
        found_item.is_approved = True
        found_item.status = 'solved'

    db.session.commit()

    return redirect(url_for('match_review'))


@app.route('/reject_match/<int:lost_id>/<int:found_id>', methods=['POST'])
def reject_match(lost_id, found_id):
    rejected = RejectedMatch(lost_id=lost_id, found_id=found_id)
    db.session.add(rejected)
    db.session.commit()
    return redirect(url_for('match_review'))

@app.route('/solved-cases')
def solved_cases():
    solved = SolvedCase.query.all()

    # Fetch corresponding LostItem and FoundItem for each solved case
    solved_data = []
    for entry in solved:
        lost_item = LostItem.query.get(entry.lost_item_id)
        found_item = FoundItem.query.get(entry.found_item_id)
        solved_data.append({
            'lost': lost_item,
            'found': found_item,
            'approved_on': entry.approved_on.strftime('%Y-%m-%d')
        })

    return render_template('solved-cases.html', solved_data=solved_data)




# -------------------- Run App --------------------

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
