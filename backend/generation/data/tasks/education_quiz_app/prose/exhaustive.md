Build a quiz app with a JSON API under `/api` and a web frontend. Anyone can draw a short random quiz of multiple-choice questions and submit answers to get a score; signed-in users keep a history of their attempts. The admin writes the questions.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/quiz` returns `count` random active questions with their four options but without `correct_option`. `count` is the number of questions (default 5, from 1 to 10; fewer when fewer exist). It answers 400 for invalid input.
- `POST /api/attempts` scores a list of `answers` (each a `question_id` and the `selected` option `A` to `D`) and returns the score with every answered question, its correct option and `is_correct`; for a signed-in user the attempt is saved. `score` is the percentage of correct answers rounded to a whole number. Every `question_id` must be an active question and appear at most once, and at least one answer is required. For a visitor `id` is `null` and nothing is stored. On success it answers 201. It answers 400 for invalid input.
- `GET /api/attempts` lists the signed-in user's saved attempts, newest first.
- `GET /api/attempts/{id}` returns one saved attempt with the same review as the submit response. It answers 404 for an unknown attempt or one of another user.
- `POST /api/admin/questions` adds a question, for the admin. On success it answers 201. It answers 400 for invalid input.
- `GET /api/admin/questions` lists every question with its correct option, for the admin. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).

Data:

- An attempt is returned as an object with `id`, `score`, `correct`, `total_questions`, `submitted_at`, `answers`.
- A question is returned as an object with `id`, `text`, `option_a`, `option_b`, `option_c`, `option_d`, `correct_option`.
- `GET /api/quiz` returns `{"items": [...]}`; each item in `items` is an object with `id`, `text`, `option_a`, `option_b`, `option_c` and `option_d`.
- `GET /api/attempts` returns `{"items": [...]}`; each item in `items` is an attempt.
- `GET /api/admin/questions` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a question.

Validation (invalid input answers 400 and changes nothing):

- Question: `text` required, at most 500 characters; `option_a` required, at most 200 characters; `option_b` required, at most 200 characters; `option_c` required, at most 200 characters; `option_d` required, at most 200 characters; `correct_option` required, one of `A`, `B`, `C`, `D`.

Rules:

- The correct option of a question is never part of a quiz draw; it is revealed only in the result of a submitted attempt.
- Each user sees only their own saved attempts.
- On first start the app holds at least 20 general-knowledge questions.

Edge cases:

- A quiz draw never repeats a question.
- Submitting the same question twice in one attempt answers 400.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- One question at a time with its options as radio buttons, a progress indicator ('Question 2 of 5'), 'Next' and, on the last question, 'Submit'.
- A result view with the score, the number correct and every question with the chosen and the correct option marked; visitors see a 'Sign in to save your results' prompt.
- For signed-in users: the saved attempts (date, score, number of questions); opening one shows its review.
- For the admin: an 'Add question' form showing the API's validation messages and the question list.
