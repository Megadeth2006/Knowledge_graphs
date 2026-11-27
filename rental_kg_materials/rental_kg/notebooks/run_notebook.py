"""Execute a notebook top to bottom without Jupyter and store the outputs."""

import argparse
import ast
import base64
import contextlib
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path


def cell_source(cell: dict) -> str:
    source = cell["source"]
    return "".join(source) if isinstance(source, list) else source


def figures() -> list[dict]:
    import matplotlib.pyplot as plt

    outputs = []
    for number in plt.get_fignums():
        buffer = io.BytesIO()
        plt.figure(number).savefig(buffer, format="png", dpi=110, bbox_inches="tight")
        data = base64.b64encode(buffer.getvalue()).decode("ascii")
        outputs.append(
            {
                "output_type": "display_data",
                "data": {"image/png": data, "text/plain": ["<Figure>"]},
                "metadata": {},
            }
        )
    plt.close("all")
    return outputs


def run_cell(code: str, namespace: dict, count: int) -> tuple[list[dict], bool]:
    tree = ast.parse(code)
    last = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = ast.Expression(tree.body.pop().value)
    stdout, stderr = io.StringIO(), io.StringIO()
    outputs, ok, value = [], True, None
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            exec(compile(tree, f"<cell {count}>", "exec"), namespace)
            if last is not None:
                value = eval(compile(last, f"<cell {count}>", "eval"), namespace)
        except Exception as error:  # noqa: BLE001 - report like Jupyter
            ok = False
            outputs.append(
                {
                    "output_type": "error",
                    "ename": type(error).__name__,
                    "evalue": str(error),
                    "traceback": traceback.format_exc().splitlines(),
                }
            )
    for name, stream in (("stdout", stdout), ("stderr", stderr)):
        if stream.getvalue():
            outputs.insert(
                0, {"output_type": "stream", "name": name, "text": stream.getvalue()}
            )
    outputs += figures()
    if value is not None:
        outputs.append(
            {
                "output_type": "execute_result",
                "execution_count": count,
                "data": {"text/plain": repr(value)},
                "metadata": {},
            }
        )
    return outputs, ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notebook", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("MPLBACKEND", "Agg")
    path = args.notebook.resolve()
    notebook = json.loads(path.read_text(encoding="utf-8"))
    os.chdir(path.parent)
    sys.path.insert(0, str(path.parent))
    namespace = {"__name__": "__main__"}
    count, started = 0, time.time()
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        count += 1
        cell["execution_count"] = count
        cell["outputs"], ok = run_cell(cell_source(cell), namespace, count)
        if not ok:
            print(f"Ячейка {count} завершилась ошибкой", file=sys.stderr)
            break
    notebook["metadata"]["executed_by"] = (
        f"run_notebook.py, Python {sys.version.split()[0]}, {time.time() - started:.0f} s"
    )
    path.write_text(
        json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"{count} ячеек выполнено за {time.time() - started:.0f} с -> {path}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
