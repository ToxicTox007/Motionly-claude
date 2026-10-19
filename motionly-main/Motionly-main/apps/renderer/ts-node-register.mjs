// Register the ts-node ESM hooks through Node's supported module.register()
// API. Use with `node --import ./ts-node-register.mjs <entry>.ts` instead of the
// experimental `--loader ts-node/esm` flag. TS_NODE_* environment variables
// (for example TS_NODE_TRANSPILE_ONLY and TS_NODE_PROJECT) still apply.
import { register } from "node:module";
import { pathToFileURL } from "node:url";

register("ts-node/esm", pathToFileURL("./"));
