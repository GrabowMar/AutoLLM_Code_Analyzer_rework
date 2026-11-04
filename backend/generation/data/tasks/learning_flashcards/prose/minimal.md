Build a flashcard app. Anyone browses shared cards grouped in decks; signed-in users write cards, edit or delete their own, and study: answering right moves a card up one of five boxes, answering wrong sends it back to box 1, per user.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/flashcards, POST /api/flashcards, GET /api/flashcards/{id}, PUT /api/flashcards/{id}, DELETE /api/flashcards/{id}, POST /api/flashcards/{id}/answers, GET /api/decks, GET /api/study.
