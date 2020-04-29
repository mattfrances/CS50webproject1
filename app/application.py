import os
import requests

from flask import Flask, session, render_template, redirect, url_for, request, jsonify
from flask_session import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from app.helpers import login_required, hash_password, verify_password

app = Flask(__name__)

# Check for environment variable
if not os.getenv("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL is not set")

# Configure session to use filesystem
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Set up database
engine = create_engine(os.getenv("DATABASE_URL"))
db = scoped_session(sessionmaker(bind=engine))


@app.route('/', methods=["GET", "POST"])
@app.route('/search', methods=["GET", "POST"])
@login_required
def index():
    """Page which allows the user to search for books by title, author or isbn."""

    message = None

    # Render page with no search results.
    if request.method == "GET":
        return render_template('index.html', books=[], message=message)
    
    # Render the results from the user's search.
    if request.method == "POST":
        # Remove left and right whitespace.
        book = request.form.get('book')
        book = book.lstrip()
        book = book.rstrip()

        # Find all books that have the searched value in their title, author or isbn.
        books = db.execute("SELECT * from books WHERE isbn ILIKE :book OR title ILIKE :book OR author ILIKE :book", { "book": f"%{book}%" }).fetchall()
        message = "Sorry, there are no matches for your search." if len(books) == 0 else ""
        return render_template('index.html', books=books, message=message)


@app.route('/register', methods=["GET", "POST"])
def register():
    """Register a user to the book club."""

    # If the user is already logged in, they will be redirected to the index page.
    if request.method == "GET" and session.get('user_id') is not None:
        return redirect(url_for('index'))

    error = None

    # Register the user to the book club.
    if request.method == "POST":
        # Check that both the username and password are filled in.
        if request.form.get('username') and request.form.get('password'):
            if db.execute("SELECT * FROM person WHERE username = :username", {"username": request.form['username']}).rowcount == 0:
                hashed_pw = hash_password(request.form['password'])
                db.execute("INSERT INTO person (username, password) VALUES (:username, :password)", {"username": request.form['username'], "password": hashed_pw})
                db.commit()
                return redirect(url_for('login'))
            else:
                error = "A user already exists with this username. Please pick something new."
        else:
            error = "Please fill in all fields."
    return render_template('register.html', error=error)
   

@app.route("/login", methods=["GET", "POST"])
def login():
    """Log a user in."""

    # If the user is already logged in, they will be redirected to the index page.
    if request.method == "GET" and session.get('user_id') is not None:
        return redirect(url_for('index'))
    
    error = None

    # Log the user in.
    if request.method == "POST":
        # Check that both the username and password are filled in.
        if request.form.get('username') and request.form.get('password'):
            user = db.execute("SELECT * FROM person WHERE username = :username", {"username": request.form['username']}).fetchone()
            db.commit()
            if not user or not verify_password(user.password, request.form['password']):
                error = "Incorrect username or password. Please try again."
            else:
                session["user_id"] = user.person_id
                return redirect(url_for('index'))
        else:
            error = "Please fill in all fields."
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    """Log the user out."""

    session.clear() # Clear all session variables.
    db.close()
    return redirect(url_for('login'))


@app.route("/book/<id>", methods=["GET", "POST"])
@login_required
def bookDetails(id):
    """Generate the details for a book based on the book id."""

    # Upon a POST request, add the review to the database before loading all new reviews from database.
    if request.method == "POST":
        book_id = id
        user_id = session.get('user_id')
        review = request.form.get("review")
        rating = request.form.get("rating")
        error = None

        # If the review or rating field was not entered or the user has already created a review for this book, do not add the review to the database.
        if not review or not rating or (review and rating and db.execute("SELECT * FROM reviews WHERE person_id = :id AND book_id = :book_id", {"id": user_id, "book_id": book_id}).rowcount != 0):
            error = "There is a maximum of 1 review per person per book. To submit a review, please enter a review and a rating."

        # If all is good, slap that review into the database.
        if not error:
            db.execute("INSERT INTO reviews (book_id, person_id, review, rating) VALUES (:book_id, :person_id, :review, :rating)", {
                "book_id": book_id,
                "person_id": user_id,
                "review": review,
                "rating": rating
            })
            db.commit()

    # Get the general details about 
    details = db.execute("SELECT * from books where id = :id", { "id": id }).fetchone()
    db.commit()

    # If the book does not exist, render an empty details array. The HTML logic will create an error message for it.
    if not details:
        return render_template('bookDetails.html', details=[])

    # If the book does exist, get the reviews from the database and the rating average from Goodreads API.
    reviews = db.execute("SELECT reviews.review, reviews.rating, person.username FROM reviews INNER JOIN person on reviews.person_id = person.person_id WHERE reviews.book_id = :id", {"id": id}).fetchall()
    db.commit()
    rating_average = 0
    res = requests.get("https://www.goodreads.com/book/review_counts.json", params={"key": "5p7GYRTHMoxkrx6HSbFg", "isbns": details.isbn})
    if res.status_code == 200:
        rating_average = res.json()['books'][0]['average_rating']

    if request.method == "GET":
        return render_template('bookDetails.html', details=details, reviews=reversed(reviews), rating_average=rating_average)

    if request.method == "POST":
        return render_template('bookDetails.html', details=details, reviews=reversed(reviews), rating_average=rating_average, error=error)


@app.route('/api/<isbn>', methods=["GET"])
def api_book_info(isbn):
    """Return details about a single book."""

    # Make sure book exists.
    book = db.execute("SELECT * from books where isbn = :isbn", { "isbn": isbn }).fetchone()
    if book is None:
        return jsonify({"error": "Invalid isbn"}), 422

    # Get info associated to book.
    reviews = db.execute("SELECT * from reviews where book_id = :book_id", {"book_id": book.id})
    review_count = 0
    average_score = 0
    for review in reviews:
        review_count += 1
        average_score += review.rating
    
    # Ensure no division by 0 error.
    if average_score != 0 and review_count != 0:
        average_score /= review_count

    return jsonify({
        "title": book.title,
        "author": book.author,
        "publication_date": book.year,
        "isbn": book.isbn,
        "review_count": review_count,
        "average_score": average_score
    })
