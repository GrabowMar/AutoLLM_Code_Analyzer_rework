Build a shared book catalogue with reader reviews, with a JSON API under `/api` and a web frontend. Anyone can browse books and reviews; signed-in users add, edit and delete books and review each book once.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/books` lists books, newest first, as a paged list. `q` matches title or author case-insensitively as a substring; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/books` adds a book. On success it answers 201. It answers 400 for invalid input.
- `GET /api/books/{id}` returns one book. It answers 404 for an unknown id.
- `PUT /api/books/{id}` replaces a book's fields; an omitted `year` or `isbn` is cleared. It answers 400 for invalid input; 404 for an unknown id.
- `DELETE /api/books/{id}` deletes a book and its reviews. On success it answers 204. It answers 404 for an unknown id.
- `GET /api/books/{id}/reviews` lists a book's reviews, newest first. It answers 404 for an unknown id.
- `POST /api/books/{id}/reviews` adds the signed-in user's review of a book. On success it answers 201. It answers 400 for invalid input; 404 for an unknown id; 409 when the user already reviewed the book.

Data:

- A book is returned as an object with `id`, `title`, `author`, `year`, `isbn`, `created_at`, `review_count`, `average_rating`.
- A review is returned as an object with `id`, `book_id`, `rating`, `comment`, `created_at`, `username`.
- `GET /api/books` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a book.
- `GET /api/books/{id}/reviews` returns `{"items": [...]}`; each item in `items` is a review.

Validation (invalid input answers 400 and changes nothing):

- Book: `title` required, at most 255 characters; `author` required, at most 255 characters; `year` optional, a whole number from 1000 to the current year; `isbn` optional, at most 20 characters.
- Review: `rating` required, a whole number from 1 to 5; `comment` optional, at most 1000 characters.

Rules:

- Any signed-in user may change any book.
- A user reviews a book at most once.
- `average_rating` is the mean rating rounded to one decimal, or `null` without reviews.

Edge cases:

- A year in the future answers 400.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A book table with title, author, year and average rating, a search box and paging.
- A book page with its reviews (username, rating, comment, date) and, for signed-in users, a rating form.
- Add and edit forms that show the API's validation messages; delete asks for confirmation.
