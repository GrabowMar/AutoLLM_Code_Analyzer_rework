const cors = require("cors");

app.use(cors({ origin: "https://app.example.com", credentials: true }));
app.use(cors());
