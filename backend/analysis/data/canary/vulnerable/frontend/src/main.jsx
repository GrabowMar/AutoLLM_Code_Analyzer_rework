import { createRoot } from "react-dom/client";
import axios from "axios";
import App from "./App";

axios.defaults.baseURL = "/api";
createRoot(document.getElementById("root")).render(<App />);
