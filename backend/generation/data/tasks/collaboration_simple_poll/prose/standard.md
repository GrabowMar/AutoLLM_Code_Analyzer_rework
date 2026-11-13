Build a quick-poll app with a JSON API under `/api` and a web frontend. Signed-in users create polls with 2 to 6 options; anyone can vote and see the results.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/polls` creates a poll for the signed-in user.
- `GET /api/polls` lists polls with their options, newest first, as a paged list (query parameters: `page`, `page_size`).
- `GET /api/polls/{id}` returns one poll with its options.
- `POST /api/polls/{id}/vote` takes the `position` of the chosen option (1 for the first), adds one vote to it and returns the updated poll.
- `DELETE /api/polls/{id}` deletes one of the signed-in user's polls with its votes.

Data:

- A poll is returned as an object with `id`, `question`, `created_at`, `options`, `total_votes`.
- `GET /api/polls` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a poll.

Rules:

- Every poll in a response carries its options (`id`, `position`, `text`, `votes`) in position order and `total_votes`.
- Every call to the vote endpoint counts as one vote.
- Only the creator may delete a poll.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Each poll shows its question and a vote button per option; after voting it switches to its results.
- Results as a horizontal bar per option with its vote count and share in percent.
- A create form whose option fields can be added (up to six) and removed (down to two), showing the API's validation messages; a delete button on the user's own polls.
- The browser remembers which polls this visitor voted on and shows those as results.
