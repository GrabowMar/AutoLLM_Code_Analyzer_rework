import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function TodoList({ version }) {
  const [items, setItems] = useState([]);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api(`/todos?q=${encodeURIComponent(query)}`).then((data) => setItems(data.items));
  }, [query, version]);

  async function toggle(todo) {
    const updated = await api(`/todos/${todo.id}`, { method: "PATCH", body: { done: !todo.done } });
    setItems(items.map((item) => (item.id === updated.id ? updated : item)));
  }

  return (
    <section>
      <input placeholder="Search" value={query} onChange={(e) => setQuery(e.target.value)} />
      <ul>
        {items.map((todo) => (
          <li key={todo.id}>
            <label>
              <input type="checkbox" checked={todo.done} onChange={() => toggle(todo)} />
              {todo.title}
            </label>
          </li>
        ))}
      </ul>
    </section>
  );
}
