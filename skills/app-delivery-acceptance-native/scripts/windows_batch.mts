/** Launch Windows batch files through cmd without applying a shell to ordinary executables.
 * Argument escaping adapted from cross-spawn (MIT); see windows_batch.LICENSE.
 */
import { existsSync, statSync } from "node:fs";
import { delimiter, extname, resolve, sep } from "node:path";

function variable(env: NodeJS.ProcessEnv, name: string) {
  return Object.entries(env).find(([key]) => key.toUpperCase() === name)?.[1];
}
function resolveBatch(command: string, cwd: string, env: NodeJS.ProcessEnv) {
  const extension = extname(command);
  if (extension && !/\.(?:cmd|bat)$/i.test(extension)) return null;
  const extensions = extension ? [""] : ["", ...(variable(env, "PATHEXT") ?? ".COM;.EXE;.BAT;.CMD").split(";")];
  const bases = /[\\/:]/.test(command) ? [resolve(cwd, command)] : [
    resolve(cwd, command),
    ...(variable(env, "PATH") ?? "").split(delimiter).map(dir => resolve(cwd, dir.replace(/^"|"$/g, ""), command)),
  ];
  for (const base of bases) {
    for (const suffix of extensions) {
      const filename = base + suffix;
      if (existsSync(filename) && statSync(filename).isFile()) {
        return /\.(?:cmd|bat)$/i.test(filename) ? filename : null;
      }
    }
  }
  return null;
}
const shellMeta = /[()[\]%!^"\x60<>&|;, *?]/g;
const shellEscape = (text: string) => text.replace(shellMeta, "^$&");
function quoteArgument(text: string, shim: boolean) {
  // Escape quotes for Windows argv, then escape cmd metacharacters.
  const quoted = '"' + text.replace(/(?=(\\+?)?)\1"/g, '$1$1\\"').replace(/(?=(\\+?)?)\1$/, "$1$1") + '"';
  const once = shellEscape(quoted);
  return shim ? shellEscape(once) : once;
}
export function windowsBatch(command: string[], cwd: string, env = process.env) {
  if (sep !== "\\") return null;
  const file = resolveBatch(command[0], cwd, env);
  if (!file) return null;
  if (command.some(arg => /[\r\n]/.test(arg))) throw new Error("Windows batch arguments must fit on one line");
  const shim = /node_modules[\\/]\.bin[\\/][^\\/]+\.cmd$/i.test(file);
  const invocation = [shellEscape(file), ...command.slice(1).map(arg => quoteArgument(arg, shim))].join(" ");
  return { executable: variable(env, "COMSPEC") ?? variable(process.env, "COMSPEC") ?? "cmd.exe",
    args: ["/d", "/s", "/v:off", "/c", '"' + invocation + '"'] };
}
