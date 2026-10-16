"""规则目录的读取与「启用/停用」的文本级编辑。

★ 为什么单独一个 channel
------------------------
和 engine / overlay 两个 channel 不同，这里**没有子进程、没有定时器、
没有信号**，所以刻意**不 import Qt** —— 纯逻辑类可以不启动 Qt 就被单测覆盖。

★ 核心约束：写回时**文件其余部分一字不动**
------------------------------------------
规则 TOML 里有大量手写注释（比如 combat.toml 头部那 20 行"已知限制"说明、
每条规则上面的"解决：…／依赖：…"）。这些注释是**人类知识**，不是噪音。

所以本模块**从不重新序列化整个文件**（tomllib 只读不写，而且即使能写，
重新序列化也会把注释、字段顺序、空行全部毁掉）。改 enabled 走纯文本操作：

    1. ``tomllib`` 解析 -> 拿到结构化数据（顺序与文件里 ``[[rules]]``
       出现的顺序一致，两者按下标一一对应）
    2. 逐行扫出**未被注释**的表头，定位每个 ``[[rules]]`` 块的行区间
    3. 只改那一块里的 ``enabled`` 那一行（或插一行），其余行原样保留

★ enabled 的三态约定
-------------------
    没有 enabled 字段  ->  默认启用（不写噪音）
    enabled = false    ->  停用
    enabled = true     ->  启用（手写者可能这么写）

用户勾选 = **删掉** enabled 字段回到默认；取消勾选 = 写入 ``enabled = false``。
这样文件里永远不会堆积一堆 ``enabled = true``。

★ 注释掉的 [[rules]] 必须忽略
------------------------------
combat.toml / nav.toml 末尾有整块注释掉的占位规则。按文本搜索规则名会
命中注释行 —— 本模块只认**行首非 ``#``** 的表头，从根上避开这个坑。
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: 规则目录相对引擎根的位置
RULES_SUBDIR = ("autopilot", "rules")

#: 表头：``[table]`` 或 ``[[table]]``，允许行尾注释
_TABLE_RE = re.compile(r"^\s*(\[\[?)\s*([A-Za-z0-9_.\"'-]+)\s*(\]\]?)\s*(#.*)?$")
#: 键值行：``name = ...``。用来判断"这一行属于某个块的键"
_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")
#: 现有的 enabled 行（行尾注释另存）
_ENABLED_RE = re.compile(r"^(\s*)enabled\s*=\s*(true|false)(\s*(#.*)?)?$",
                         re.IGNORECASE)
#: name 行。**只用于改写前交叉校验**，改写本身永远不碰 name。
_NAME_RE = re.compile(r"^name\s*=\s*([\"'])(.*?)\1\s*(#.*)?$")


class RuleEditError(RuntimeError):
    """改规则失败。**一律带人话原因** —— UI 直接把它显示给用户。"""


# ======================================================================
# 数据结构
# ======================================================================
@dataclass
class RuleInfo:
    """一条规则（供 UI 显示）。字段来自 TOML，全部容忍缺失。"""

    name: str
    priority: int | None
    scenes: list[str]
    note: str
    #: 解析后的实际启用状态（缺字段 = 默认启用）
    enabled: bool
    #: TOML 里是否**显式**写了 enabled —— 决定取消勾选是"改值"还是"插一行"
    has_enabled_key: bool

    @property
    def scenes_text(self) -> str:
        """场景列文案。空列表显示 ``—`` 而不是留白 ——
        留白会让人以为是加载失败。"""
        return ", ".join(self.scenes) if self.scenes else "—"

    @property
    def priority_text(self) -> str:
        return "—" if self.priority is None else str(self.priority)


@dataclass
class RuleFile:
    """一个规则文件。``error`` 非 None 时 ``rules`` 为空。"""

    path: Path
    rules: list[RuleInfo] = field(default_factory=list)
    error: str | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def enabled_count(self) -> int:
        return sum(1 for r in self.rules if r.enabled)


# ======================================================================
# 文本工具
# ======================================================================
def rules_dir(engine_root: Path | str) -> Path:
    """规则目录 = <引擎根>/autopilot/rules。"""
    return Path(engine_root).joinpath(*RULES_SUBDIR)


def _read_text(path: Path) -> str:
    """读原文，**保留原始换行符**（``newline=""`` 不做 CRLF 翻译）。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write_atomic(path: Path, text: str) -> None:
    """原子写。**先写临时文件再 replace** —— 中途崩了不会留下半个 TOML，
    那会直接让引擎下次启动解析失败。"""
    tmp = path.with_name(path.name + ".neko-tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _parse_toml(text: str) -> dict:
    """解析 TOML 文本。**只喂解析器用，不拿去写回。**

    ★ BOM 处理
      带 UTF-8 BOM 的文件，``tomllib`` 会报 "Expected '=' after a key in a
      key/value pair"（它把 BOM 当成了一个非法字符），但引擎那边多半是能
      读的。所以解析时先把 BOM 摘掉，**写回时仍用原始文本** —— 绝不因为
      顺手读一次就把文件编码改掉。
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    return tomllib.loads(text)


def _split_eol(line: str) -> tuple[str, str]:
    """拆出 (正文, 行尾符)。行尾符可能是 ``\\r\\n`` / ``\\n`` / ``\\r`` /
    空（原文件最后一行没换行）。"""
    for eol in ("\r\n", "\n", "\r"):
        if line.endswith(eol):
            return line[: -len(eol)], eol
    return line, ""


def _iter_table_blocks(lines: list[str]):
    """产出 ``(表名, 起始行下标, 结束行下标)``，**只认未被注释的表头**。

    ★ 跳过以 ``#`` 开头的行是本模块最关键的一步 ——
      combat.toml / nav.toml 里整块注释掉的 ``# [[rules]]`` 占位规则，
      不跳过就会把它们当成真规则列到 UI 上（8 条变 11 条）。
    """
    starts: list[tuple[int, str]] = []
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = _TABLE_RE.match(raw)
        if m:
            starts.append((i, m.group(2)))

    for idx, (start, name) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        yield name, start, end


def _block_keys(lines: list[str], start: int, end: int) -> dict[str, int]:
    """块内 ``键名 -> 行下标``。只认真正的键值行，跳过空行/注释。"""
    keys: dict[str, int] = {}
    for i in range(start + 1, end):
        if not lines[i].strip() or lines[i].strip().startswith("#"):
            continue
        m = _KEY_RE.match(lines[i])
        if m:
            keys[m.group(1)] = i
    return keys


def _insert_position(lines: list[str], start: int, end: int,
                     keys: dict[str, int]) -> int:
    """新 ``enabled`` 行插在哪。

    优先插到 ``note`` **之前** —— 这是三个规则文件里手写者的既有惯例
    （``enabled = false`` 正好夹在 scenes 和 note 中间）。没有 note 时
    退到"块内最后一个键之后"，绝不动块外的注释（注释往往描述的是
    下一条规则，插错了会把它和规则割裂开）。
    """
    if "note" in keys:
        return keys["note"]
    last = start
    for i in range(start + 1, end):
        if _KEY_RE.match(lines[i]):
            last = i
    return last + 1


# ======================================================================
# 仓库
# ======================================================================
class RulesRepository:
    """规则目录的读写门面。

    ★ 构造参数**两种都收**
    ----------------------
    传进来的可以是**引擎根**（``.../genshin_game_buddy``），也可以**直接是
    规则目录**（``.../autopilot/rules``）。靠"里面有没有 *.toml"自动判断。

    为什么不只收一种：只收引擎根的话，任何人（包括调试脚本）把规则目录
    传进来就会得到 ``<rules>/autopilot/rules`` 这种叠了三层的假路径，
    而 ``Path.glob`` 对不存在的目录**不抛异常、只是静悄悄地返回空** ——
    于是 ``scan()`` 返回 ``[]``，看起来像"规则文件没了"。
    那种错误必须从构造函数这一层就掐死，不能留给调用方去猜。
    """

    def __init__(self, root: Path | str) -> None:
        self.engine_root = Path(root)
        self._explicit_dir: Path | None = (
            self.engine_root if self._looks_like_rules_dir(self.engine_root)
            else None
        )

    @staticmethod
    def _looks_like_rules_dir(p: Path) -> bool:
        """这个路径本身是不是规则目录。

        ★ 判据顺序不能反
        ------------------
        "里面有没有 *.toml" 这条判据**不够独特** —— 引擎根
        （``.../genshin_game_buddy``）顶层同样躺着好几个 .toml 配置文件。
        先用"引擎根"这条更明确的特征（下面存在 ``autopilot/rules``）
        把它排除掉，剩下的才当成规则目录。
        顺序写反的结果是：引擎根被当成规则目录，扫出一堆零规则的文件，
        状态栏显示"加载了 4 个文件，0 条规则"。
        """
        if not p.is_dir():
            return False
        if (p / "autopilot" / "rules").is_dir():
            return False                  # 这就是引擎根，不可能是规则目录
        if p.name == "rules":             # 目录名就叫 rules
            return True
        try:
            if any(p.glob("*.toml")):     # 里面直接躺着 TOML
                return True
        except OSError:
            return False
        return False

    @property
    def rules_dir(self) -> Path:
        """实际使用的规则目录。

        ★ 显式传进来的目录**原样使用**，绝不追加 ``autopilot/rules``。
        """
        if self._explicit_dir is not None:
            return self._explicit_dir
        return rules_dir(self.engine_root)

    # -- 读 ---------------------------------------------------------------
    def scan(self) -> list[RuleFile]:
        """读全部 ``*.toml``。**单个文件解析失败不影响其它文件** ——
        返回带 ``error`` 的条目，UI 负责标红。

        ★ 目录不存在时**大声记 ERROR** 再返回空表
          --------------------------------------------
          ``Path.glob`` 对不存在的目录返回空迭代器且**不抛异常**，
          所以"路径错了"和"目录里真的一条规则都没有"在返回值上完全一样。
          这里补一条 ERROR 日志把两者区分开，否则排查时只能靠猜。
        """
        d = self.rules_dir
        if not d.is_dir():
            logger.error(
                "规则目录不存在：%s（构造时传入的根 = %s）", d, self.engine_root)
            return []

        try:
            paths = sorted(p for p in d.glob("*.toml") if p.is_file())
        except OSError as exc:
            logger.error("扫描规则目录失败：%s（目录 %s）", exc, d)
            return []

        if not paths:
            logger.warning("规则目录里没有任何 *.toml：%s", d)

        out: list[RuleFile] = []
        for p in paths:
            out.append(self._read_one(p))
        return out

    def _read_one(self, path: Path) -> RuleFile:
        """读一个文件。**任何失败都变成一个带 error 的 RuleFile 返回**，
        绝不让异常冒到 scan() 去 —— 一个坏文件不该让另外两个好的一起消失。

        ★ 失败时**必须记 ERROR 日志**
          --------------------------
          只把错误塞进 ``RuleFile.error`` 给 UI 显示是不够的：用户可能
          根本没点开那个文件，日志里也就什么都没有，下次"为什么少了一个
          文件"就无从查起。
        """
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError) as exc:
            logger.error("规则文件读取失败：%s —— %s", path.name, exc)
            return RuleFile(path=path, error=f"读取失败：{exc}")

        try:
            data = _parse_toml(text)
        except tomllib.TOMLDecodeError as exc:
            detail = _format_decode_error(exc)
            logger.error("规则文件 TOML 语法错误：%s —— %s（该文件将被跳过）",
                         path.name, detail)
            return RuleFile(path=path, error=detail)
        except Exception as exc:  # noqa: BLE001 - 兜底不让 UI 崩
            logger.exception("规则文件解析异常：%s", path.name)
            return RuleFile(path=path, error=f"解析失败：{exc}")

        raw_rules = data.get("rules")
        if not isinstance(raw_rules, list):
            return RuleFile(path=path, rules=[])

        rules: list[RuleInfo] = []
        for r in raw_rules:
            if not isinstance(r, dict):
                continue
            scenes = r.get("scenes")
            rules.append(RuleInfo(
                name=str(r.get("name", "（未命名）")),
                priority=r.get("priority") if isinstance(r.get("priority"),
                                                         int) else None,
                scenes=[str(s) for s in scenes] if isinstance(scenes, list)
                else [],
                note=str(r.get("note", "")),
                # 缺字段 = 默认启用。这条约定写在模块 docstring 里。
                enabled=bool(r.get("enabled", True)),
                has_enabled_key="enabled" in r,
            ))
        return RuleFile(path=path, rules=rules)

    # -- 写 ---------------------------------------------------------------
    def set_enabled(self, path: Path | str, rule_name: str,
                    enabled: bool) -> str:
        """把某条规则的启用状态改成 ``enabled``。返回一句人话结果说明。

        :raises RuleEditError: 失败时抛出，**消息里带具体原因**（UI 直接显示）
        """
        path = Path(path)
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError) as exc:
            raise RuleEditError(f"读取 {path.name} 失败：{exc}") from exc

        # ① 结构化解析 —— 用来按**顺序**对齐文本块与规则对象
        try:
            data = _parse_toml(text)
        except tomllib.TOMLDecodeError as exc:
            raise RuleEditError(
                f"{path.name} 现在解析不了，没法安全修改："
                f"{_format_decode_error(exc)}") from exc

        parsed = data.get("rules")
        parsed = parsed if isinstance(parsed, list) else []
        names = [str(r.get("name", "")) for r in parsed
                 if isinstance(r, dict)]
        if rule_name not in names:
            raise RuleEditError(
                f"{path.name} 里已经找不到规则「{rule_name}」—— "
                f"文件可能被你手工改过，先点「重新加载」。")
        index = names.index(rule_name)

        # ② 定位同名的那一块（**跳过注释掉的表头**）
        lines = text.splitlines(keepends=True)
        blocks = [b for b in _iter_table_blocks(lines) if b[0] == "rules"]
        if index >= len(blocks):
            raise RuleEditError(
                f"{path.name} 的第 {index + 1} 条规则对不上文本块，"
                f"文件可能混了非常规写法，请先点「重新加载」。")
        _, start, end = blocks[index]
        keys = _block_keys(lines, start, end)

        # ★ 交叉校验：这块文本里的 name 必须真的是我们要改的那条
        #   上面全靠"下标一一对应"。万一遇到文本扫描出的块比 tomllib 多
        #   （比如多行字符串里恰好有 "[[rules]]" 这段字符），下标会错位，
        #   结果就是**改错了规则还一声不吭** —— 那是这个模块最坏的失败
        #   模式（用户以为自己停了 A，实际停了 B）。
        #   宁可报错让用户点「重新加载」，也不能改错。
        name_line = keys.get("name")
        if name_line is not None:
            body, _ = _split_eol(lines[name_line])
            nm = _NAME_RE.match(body.strip())
            if nm is not None and nm.group(2) != rule_name:
                raise RuleEditError(
                    f"{path.name} 第 {index + 1} 条规则对不上："
                    f"文本里写的是「{nm.group(2)}」，要改的是「{rule_name}」。"
                    f"为避免改错规则已中止，请先点「重新加载」。")

        # ③ 只动那一行
        if "enabled" in keys:
            body, eol = _split_eol(lines[keys["enabled"]])
            m = _ENABLED_RE.match(body.strip())
            if m is None:
                raise RuleEditError(
                    f"{path.name} 里「{rule_name}」的 enabled 那一行"
                    f"写法不是 enabled = true/false，认不出，没动它。")
            cur = m.group(2).lower() == "true"
            if enabled:
                if cur:
                    return f"「{rule_name}」本来就写着 enabled = true，没动。"
                # ★ 回到默认 = **删掉**这个键，而不是写 enabled = true
                del lines[keys["enabled"]]
                action = "已删除 enabled = false（恢复默认启用）"
            else:
                if not cur:
                    return f"「{rule_name}」已经是 enabled = false，没动。"
                indent = m.group(1)
                comment = (m.group(3) or "").strip()
                tail = f"  {comment}" if comment else ""
                lines[keys["enabled"]] = \
                    f"{indent}enabled = false{tail}{eol}"
                action = "已改成 enabled = false"
        else:
            if enabled:
                return f"「{rule_name}」本来就是默认启用（文件里没写 enabled），没动。"
            pos = _insert_position(lines, start, end, keys)
            _, eol = _split_eol(lines[start])
            if not eol:                       # 末行无换行符时的兜底
                eol = "\r\n" if "\r\n" in text else "\n"
            lines.insert(pos, f"enabled = false{eol}")
            action = "已新增 enabled = false"

        try:
            _write_atomic(path, "".join(lines))
        except OSError as exc:
            raise RuleEditError(f"写回 {path.name} 失败：{exc}") from exc

        logger.info("规则 %s 的启用状态已改：%s", rule_name, action)
        return action


def _format_decode_error(exc: "tomllib.TOMLDecodeError") -> str:
    """把 tomllib 的异常变成「第几行 + 什么原因」。

    3.11 的 ``TOMLDecodeError`` 自带 ``lineno``/``colno``/``msg``，
    但仍用 getattr 兜底 —— 不同 Python 版本字段并不一致，
    缺了顶多少个行号，不该因此把整个文件判成读不了。
    """
    line = getattr(exc, "lineno", 0) or 0
    msg = getattr(exc, "msg", None) or str(exc)
    col = getattr(exc, "colno", 0) or 0
    where = f"第 {line} 行" if line else "位置未知"
    if col:
        where += f"，第 {col} 列"
    return f"{where}：{msg}"


__all__ = [
    "RULES_SUBDIR",
    "RuleEditError",
    "RuleFile",
    "RuleInfo",
    "RulesRepository",
    "rules_dir",
]
