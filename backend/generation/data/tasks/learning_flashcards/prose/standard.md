Build a flashcard app with a JSON API under `/api` and a web frontend. Anyone can browse the shared cards; signed-in users write cards and study them. Each user's progress per card is kept in five boxes (a simple spaced-repetition scheme).

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/flashcards` lists cards, newest first, as a paged list (query parameters: `q`, `deck`, `page`, `page_size`).
- `POST /api/flashcards` adds a card authored by the signed-in user.
- `GET /api/flashcards/{id}` returns one card.
- `PUT /api/flashcards/{id}` replaces one of the user's cards; an omitted `deck` is cleared.
- `DELETE /api/flashcards/{id}` deletes one of the user's cards and everyone's progress on it.
- `POST /api/flashcards/{id}/answers` records the user's answer (`correct` true or false) and returns their updated progress on the card.
- `GET /api/decks` lists every deck name in use with its `card_count`, sorted by name.
- `GET /api/study` returns the user's study queue: cards ordered by box (lowest first), then never-studied cards before the least recently studied ones, each with the user's progress (query parameters: `deck`, `limit`).

Data:

- A card is returned as an object with `id`, `question`, `answer`, `deck`, `created_at`, `author`.
- A progress record is returned as an object with `id`, `box`, `correct_count`, `incorrect_count`, `last_studied_at`, `flashcard_id`.
- `GET /api/flashcards` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a card.
- `GET /api/decks` returns `{"items": [...]}`; each item in `items` is an object with `name` and `card_count`.
- `GET /api/study` returns `{"items": [...], "total": n}`; each item in `items` is an object with `card` and `progress`.

Rules:

- Only a card's author may change or delete it.
- Progress is kept per user and card; a card the user never answered counts as box 1.
- Cards without a deck are not listed under decks.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A card list with a search box, a deck filter and paging; decks with their card counts.
- A study view that shows one card's question, reveals the answer on click, and offers 'I knew it' and 'I didn't' buttons.
- Forms to add and edit the user's own cards with the API's validation messages; delete with confirmation.
