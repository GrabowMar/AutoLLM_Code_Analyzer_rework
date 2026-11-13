Build a shared book catalogue with reader reviews, with a JSON API under `/api` and a web frontend. Anyone can browse books and reviews; signed-in users add, edit and delete books and review each book once.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/books` lists books, newest first, as a paged list (query parameters: `q`, `page`, `page_size`).
- `POST /api/books` adds a book.
- `GET /api/books/{id}` returns one book.
- `PUT /api/books/{id}` replaces a book's fields; an omitted `year` or `isbn` is cleared.
- `DELETE /api/books/{id}` deletes a book and its reviews.
- `GET /api/books/{id}/reviews` lists a book's reviews, newest first.
- `POST /api/books/{id}/reviews` adds the signed-in user's review of a book.

Data:

- A book is returned as an object with `id`, `title`, `author`, `year`, `isbn`, `created_at`, `review_count`, `average_rating`.
- A review is returned as an object with `id`, `book_id`, `rating`, `comment`, `created_at`, `username`.
- `GET /api/books` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a book.
- `GET /api/books/{id}/reviews` returns `{"items": [...]}`; each item in `items` is a review.

Rules:

- Any signed-in user may change any book.
- A user reviews a book at most once.
- `average_rating` is the mean rating rounded to one decimal, or `null` without reviews.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A book table with title, author, year and average rating, a search box and paging.
- A book page with its reviews (username, rating, comment, date) and, for signed-in users, a rating form.
- Add and edit forms that show the API's validation messages; delete asks for confirmation.
