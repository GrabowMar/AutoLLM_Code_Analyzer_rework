Build a recipe book with a JSON API under `/api` and a web frontend. Anyone can browse recipes; signed-in users add recipes and edit or delete their own.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/recipes` adds a recipe authored by the signed-in user.
- `GET /api/recipes` lists recipes, newest first, as a paged list without ingredients and instructions (query parameters: `category`, `q`, `page`, `page_size`).
- `GET /api/recipes/{id}` returns the full recipe.
- `PUT /api/recipes/{id}` replaces a recipe's fields; only its author may do this.
- `DELETE /api/recipes/{id}` deletes a recipe; only its author may do this.

Data:

- A recipe is returned as an object with `id`, `title`, `ingredients`, `instructions`, `category`, `prep_minutes`, `servings`, `created_at`, `author`.
- `GET /api/recipes` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an object with `id`, `title`, `category`, `prep_minutes` and `author`.

Rules:

- Everyone may read recipes; only a recipe's author may change or delete it.
- Ingredients keep the order they were given in.
- On first start the app holds three to five sample recipes from at least two categories.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Recipe cards with title, category, prep time and author, a category dropdown, a title search and paging.
- A recipe page with the ingredients as a list and the instructions with their line breaks kept.
- Add and edit forms with ingredient rows that can be added and removed, showing the API's validation messages; delete asks for confirmation.
