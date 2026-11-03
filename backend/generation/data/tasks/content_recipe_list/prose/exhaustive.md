Build a recipe book with a JSON API under `/api` and a web frontend. Anyone can browse recipes; signed-in users add recipes and edit or delete their own.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/recipes` adds a recipe authored by the signed-in user. On success it answers 201. It answers 400 for invalid input.
- `GET /api/recipes` lists recipes, newest first, as a paged list without ingredients and instructions. `category` keeps one category; `q` matches the title case-insensitively as a substring; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `GET /api/recipes/{id}` returns the full recipe. It answers 404 for an unknown id.
- `PUT /api/recipes/{id}` replaces a recipe's fields; only its author may do this. An omitted `category` becomes `other`; an omitted `prep_minutes` or `servings` is cleared. It answers 400 for invalid input; 403 for a recipe of another user; 404 for an unknown id.
- `DELETE /api/recipes/{id}` deletes a recipe; only its author may do this. On success it answers 204. It answers 403 for a recipe of another user; 404 for an unknown id.

Data:

- A recipe is returned as an object with `id`, `title`, `ingredients`, `instructions`, `category`, `prep_minutes`, `servings`, `created_at`, `author`.
- `GET /api/recipes` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an object with `id`, `title`, `category`, `prep_minutes` and `author`.

Validation (invalid input answers 400 and changes nothing):

- Recipe: `title` required, at most 255 characters; `ingredients` required, a list of 1 to 50 non-empty strings of at most 200 characters each, order kept; `instructions` required, at most 10000 characters; `category` optional, one of `breakfast`, `main`, `dessert`, `snack`, `drink`, `other`, default `other`; `prep_minutes` optional, a whole number from 1 to 1440; `servings` optional, a whole number from 1 to 100.

Rules:

- Everyone may read recipes; only a recipe's author may change or delete it.
- Ingredients keep the order they were given in.
- On first start the app holds three to five sample recipes from at least two categories.

Edge cases:

- Instructions are plain text: line breaks are kept and nothing in them is interpreted as markup.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Recipe cards with title, category, prep time and author, a category dropdown, a title search and paging.
- A recipe page with the ingredients as a list and the instructions with their line breaks kept.
- Add and edit forms with ingredient rows that can be added and removed, showing the API's validation messages; delete asks for confirmation.
