import { render } from "preact";

import { bootstrap } from "./store";
import { App } from "./ui/App";
import "./styles.css";

render(<App />, document.getElementById("app")!);
void bootstrap();
