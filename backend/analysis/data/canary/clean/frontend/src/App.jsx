// CANARY FIXTURE: idiomatic React component (analyzer negative control).
import { useEffect, useState } from "react";
import BookmarkList from "./components/BookmarkList";
import { loadBookmarks } from "./bookmarks";

export default function App() {
  const [bookmarks, setBookmarks] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    loadBookmarks()
      .then(setBookmarks)
      .catch(() => setError("Could not load bookmarks"));
  }, []);

  return (
    <main>
      <h1>Bookmarks</h1>
      {error ? <p role="alert">{error}</p> : <BookmarkList items={bookmarks} />}
    </main>
  );
}
