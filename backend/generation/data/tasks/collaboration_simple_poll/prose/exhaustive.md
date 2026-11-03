Build a quick-poll app with a JSON API under `/api` and a web frontend. Signed-in users create polls with 2 to 6 options; anyone can vote and see the results.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/polls` creates a poll for the signed-in user. On success it answers 201. It answers 400 for invalid input.
- `GET /api/polls` lists polls with their options, newest first, as a paged list. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `GET /api/polls/{id}` returns one poll with its options. It answers 404 for an unknown id.
- `POST /api/polls/{id}/vote` takes the `position` of the chosen option (1 for the first), adds one vote to it and returns the updated poll. It answers 400 for an unknown position; 404 for an unknown id.
- `DELETE /api/polls/{id}` deletes one of the signed-in user's polls with its votes. On success it answers 204. It answers 403 for a poll of another user; 404 for an unknown id.

Data:

- A poll is returned as an object with `id`, `question`, `created_at`, `options`, `total_votes`.
- `GET /api/polls` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a poll.

Validation (invalid input answers 400 and changes nothing):

- Poll: `question` required, at most 500 characters; `options` optional, a list of 2 to 6 distinct option texts (ignoring case) of at most 200 characters each; when omitted or empty the poll gets the options `Yes` and `No`.

Rules:

- Every poll in a response carries its options (`id`, `position`, `text`, `votes`) in position order and `total_votes`.
- Every call to the vote endpoint counts as one vote.
- Only the creator may delete a poll.

Edge cases:

- Options keep the order they were given in.
- A list with one option, more than six, or a repeated text answers 400.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Each poll shows its question and a vote button per option; after voting it switches to its results.
- Results as a horizontal bar per option with its vote count and share in percent.
- A create form whose option fields can be added (up to six) and removed (down to two), showing the API's validation messages; a delete button on the user's own polls.
- The browser remembers which polls this visitor voted on and shows those as results.
