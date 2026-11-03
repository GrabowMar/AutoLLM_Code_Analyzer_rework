import { useState } from "react";
import { api } from "../api.js";

export default function TodoForm({ onCreated }) {
  const [title, setTitle] = useState("");

  async function submit(event) {
    event.preventDefault();
    if (!title.trim()) return;
    await api("/todos", { method: "POST", body: { title } });
    setTitle("");
    onCreated();
  }

  return (
    <form onSubmit={submit}>
      <input placeholder="New todo" value={title} onChange={(e) => setTitle(e.target.value)} />
      <button type="submit">Add</button>
    </form>
  );
}
