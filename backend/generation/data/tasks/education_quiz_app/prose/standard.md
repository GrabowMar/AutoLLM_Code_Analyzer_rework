Build a quiz app with a JSON API under `/api` and a web frontend. Anyone can draw a short random quiz of multiple-choice questions and submit answers to get a score; signed-in users keep a history of their attempts. The admin writes the questions.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/quiz` returns `count` random active questions with their four options but without `correct_option` (query parameters: `count`).
- `POST /api/attempts` scores a list of `answers` (each a `question_id` and the `selected` option `A` to `D`) and returns the score with every answered question, its correct option and `is_correct`; for a signed-in user the attempt is saved.
- `GET /api/attempts` lists the signed-in user's saved attempts, newest first.
- `GET /api/attempts/{id}` returns one saved attempt with the same review as the submit response.
- `POST /api/admin/questions` adds a question, for the admin.
- `GET /api/admin/questions` lists every question with its correct option, for the admin (query parameters: `page`, `page_size`).

Data:

- An attempt is returned as an object with `id`, `score`, `correct`, `total_questions`, `submitted_at`, `answers`.
- A question is returned as an object with `id`, `text`, `option_a`, `option_b`, `option_c`, `option_d`, `correct_option`.
- `GET /api/quiz` returns `{"items": [...]}`; each item in `items` is an object with `id`, `text`, `option_a`, `option_b`, `option_c` and `option_d`.
- `GET /api/attempts` returns `{"items": [...]}`; each item in `items` is an attempt.
- `GET /api/admin/questions` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a question.

Rules:

- The correct option of a question is never part of a quiz draw; it is revealed only in the result of a submitted attempt.
- Each user sees only their own saved attempts.
- On first start the app holds at least 20 general-knowledge questions.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- One question at a time with its options as radio buttons, a progress indicator ('Question 2 of 5'), 'Next' and, on the last question, 'Submit'.
- A result view with the score, the number correct and every question with the chosen and the correct option marked; visitors see a 'Sign in to save your results' prompt.
- For signed-in users: the saved attempts (date, score, number of questions); opening one shows its review.
- For the admin: an 'Add question' form showing the API's validation messages and the question list.
