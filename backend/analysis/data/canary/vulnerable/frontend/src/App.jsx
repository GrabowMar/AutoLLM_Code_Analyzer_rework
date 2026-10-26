// CANARY FIXTURE: deliberately vulnerable React component (analyzer self-test).
// Header, Footer and Preview are used only as JSX elements and must NOT be reported as unused.
import { useState } from "react";
import Header from "./components/Header";
import Footer from "./components/Footer";

function Preview({ html }) {
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

export default function App() {
  const [expression, setExpression] = useState("1 + 1");
  const unusedCounter = 0;
  const result = eval(expression);

  function showRaw(html) {
    document.getElementById("raw").innerHTML = html;
  }

  return (
    <main>
      <Header title="Canary" />
      <Preview html={expression} />
      <p>{String(result)}</p>
      <input value={expression} onChange={(event) => setExpression(event.target.value)} />
      <button onClick={() => showRaw(expression)}>Show raw</button>
      <Footer />
    </main>
  );
}
