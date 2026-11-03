import { useState } from "react";
import { api, setToken } from "./api.js";
import TodoForm from "./components/TodoForm.jsx";
import TodoList from "./components/TodoList.jsx";

export default function App() {
  const [loggedIn, setLoggedIn] = useState(false);
  const [credentials, setCredentials] = useState({ username: "", password: "" });
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0);

  async function login(event) {
    event.preventDefault();
    try {
      const { token } = await api("/auth/login", { method: "POST", body: credentials });
      setToken(token);
      setLoggedIn(true);
    } catch (err) {
      setError(err.message);
    }
  }

  if (!loggedIn) {
    return (
      <form onSubmit={login}>
        <h1>Todo list</h1>
        <input
          placeholder="Username"
          value={credentials.username}
          onChange={(e) => setCredentials({ ...credentials, username: e.target.value })}
        />
        <input
          type="password"
          placeholder="Password"
          value={credentials.password}
          onChange={(e) => setCredentials({ ...credentials, password: e.target.value })}
        />
        <button type="submit">Log in</button>
        {error && <p role="alert">{error}</p>}
      </form>
    );
  }
  return (
    <main>
      <h1>Todo list</h1>
      <TodoForm onCreated={() => setVersion(version + 1)} />
      <TodoList version={version} />
    </main>
  );
}
