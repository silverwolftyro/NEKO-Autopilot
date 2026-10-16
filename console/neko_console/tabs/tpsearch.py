"""传送点模糊搜索（2026-10-16）。

为什么需要
----------
:func:`mapdata.search_points` 是**包含即命中**、不排序：

    key in p.search_blob

这对「精确输入地名」够用，但用户实际会这么打：

* **打错字**：``七天神像风`` 少了横杠 → 归一化能救；``七天神仙`` → 要错字表；
* **语音识别混乱**：``封神像``（风→封）、``梦度``（蒙→梦）、``璃月``/``离月``；
* **拼音**：``qtsx`` 想搜「七天神像」；
* **只记得一半**：``风神`` 想搜 ``七天神像-风``；
* **带上地区**：``蒙德 七天神像`` 想在蒙德里找。

这些没有一条能被 ``key in blob`` 接住。

设计约束
--------
* **不引入新依赖**。没有 pypinyin / jieba / rapidfuzz（见 Console 的
  pyproject，只有 pyside6 / pydantic / tomli-w）。拼音表是
  :data:`_PINYIN` 里手写的 326 字，覆盖「传送点名称 + 国家」的全部汉字。
* **复用插件侧已验证的打分骨架**。``autopilot/perception/teleport_ocr.py``
  里的 ``_edit_distance(cap)`` / ``_sim`` / 候选串集合 ``{name} /
  {name}-{area} / {area}`` 直接照搬 —— 那是 OCR 侧踩过坑定下来的，
  两边口径必须一致，否则「屏幕上认得出来、搜索框却搜不到」。
* **每条结果都能解释**。:class:`Candidate.reasons` 说明它为什么命中，
  调试和「让用户自己判断」都靠它。

数据形态（决定了候选串怎么造）
------------------------------
936 个点、**只有 106 个不同的 name**，其中 ``传送锚点`` 一个名字就占 777 个。
所以单靠 name 搜必然糊成一团，**必须把 ``areas`` / ``country`` 拼进候选串**
—— 这也是 ``(name, areas)`` 联合键的由来。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable, Sequence

__all__ = [
    "Candidate",
    "AMBIGUITY_SCORE_GAP",
    "AMBIGUITY_TOP_SCORE",
    "build_disambiguation_question",
    "canonical_homophone",
    "needs_disambiguation",
    "normalize_query",
    "search_teleport",
    "to_initials",
    "to_pinyin",
]


# =============================================================================
# 阈值
# =============================================================================

#: 与插件侧 ``teleport_ocr.MATCH_EDIT_DISTANCE`` 保持一致。
EDIT_DISTANCE_CAP = 2
#: 与插件侧 ``teleport_ocr.MATCH_SIMILARITY`` 保持一致。
SIMILARITY_FLOOR = 0.80
#: 拼音维度允许用编辑距离的**最小长度**。首字母天然只有 2~5 个字母，
#: 短于此值时拼音维度只认前缀，不认编辑距离（见 :func:`_ladder`）。
PYMIN_EDIT_LEN = 4
#: 低于这个分就不给看了 —— 936 条全列出来比不给更糟。
SCORE_FLOOR = 0.55

#: 低于此分视为「没匹配上」，需要问用户（任务书约定的 60%）。
AMBIGUITY_TOP_SCORE = 0.60
#: top1 与 top2 相差不到这个值（绝对分，不是百分比）视为「分不清」（15%）。
AMBIGUITY_SCORE_GAP = 0.15


# =============================================================================
# 拼音表（手写，覆盖传送点 name + country 的全部 326 个汉字）
# =============================================================================

#: 汉字 -> 无声调拼音。
#:
#: ★ 为什么是手写而不是装 pypinyin
#:   任务书明确「不引入新依赖」。全表 6763 字当然要库，但本模块只需要
#:   **数据里实际出现过的**字 —— 传送点 name + country 合起来 326 个，
#:   手写这张表比引入一个运行期依赖便宜得多，也不会给打包体积添麻烦。
#:   扩数据时若出现表外的字，:func:`to_pinyin` 会原样跳过该字，
#:   不会报错，只是那一字的拼音维度失效（其它维度照常打分）。
_PINYIN: dict[str, str] = {
    "七": "qi", "万": "wan", "丘": "qiu", "中": "zhong", "临": "lin",
    "丹": "dan", "主": "zhu", "之": "zhi", "乐": "le", "乡": "xiang",
    "书": "shu", "云": "yun", "五": "wu", "井": "jing", "亚": "ya",
    "亡": "wang", "人": "ren", "仙": "xian", "代": "dai", "仲": "zhong",
    "伏": "fu", "众": "zhong", "会": "hui", "伞": "san", "传": "chuan",
    "体": "ti", "余": "yu", "借": "jie", "像": "xiang", "兆": "zhao",
    "光": "guang", "入": "ru", "冕": "mian", "冬": "dong", "冰": "bing",
    "净": "jing", "凌": "ling", "凝": "ning", "切": "qie", "列": "lie",
    "创": "chuang", "剧": "ju", "努": "nu", "北": "bei", "十": "shi",
    "千": "qian", "华": "hua", "南": "nan", "卡": "ka", "却": "que",
    "卷": "juan", "古": "gu", "叮": "ding", "咎": "jiu", "响": "xiang",
    "哐": "kuang", "啷": "lang", "回": "hui", "园": "yuan", "图": "tu",
    "圃": "pu", "土": "tu", "圣": "sheng", "地": "di", "场": "chang",
    "坊": "fang", "坞": "wu", "坠": "zhui", "垒": "lei", "城": "cheng",
    "堂": "tang", "堡": "bao", "塔": "ta", "塞": "sai", "境": "jing",
    "墟": "xu", "壁": "bi", "声": "sheng", "壳": "ke", "处": "chu",
    "夏": "xia", "夜": "ye", "天": "tian", "太": "tai", "失": "shi",
    "女": "nv", "妄": "wang", "妻": "qi", "始": "shi", "子": "zi",
    "字": "zi", "孤": "gu", "宅": "zhai", "宇": "yu", "守": "shou",
    "实": "shi", "室": "shi", "宫": "gong", "寂": "ji", "密": "mi",
    "尔": "er", "尼": "ni", "局": "ju", "居": "ju", "屋": "wu",
    "山": "shan", "岛": "dao", "岩": "yan", "岫": "xiu", "峡": "xia",
    "崖": "ya", "工": "gong", "幕": "mu", "幽": "you", "库": "ku",
    "底": "di", "庙": "miao", "府": "fu", "废": "fei", "庭": "ting",
    "异": "yi", "弈": "yi", "引": "yin", "弥": "mi", "归": "gui",
    "录": "lu", "形": "xing", "影": "ying", "彼": "bi", "径": "jing",
    "待": "dai", "得": "de", "德": "de", "忘": "wang", "念": "nian",
    "态": "tai", "悬": "xuan", "想": "xiang", "戍": "shu", "所": "suo",
    "挪": "nuo", "揭": "jie", "散": "san", "敷": "fu", "新": "xin",
    "旋": "xuan", "无": "wu", "昏": "hun", "星": "xing", "景": "jing",
    "晴": "qing", "曜": "yao", "曲": "qu", "月": "yue", "有": "you",
    "望": "wang", "木": "mu", "末": "mo", "机": "ji", "枢": "shu",
    "枫": "feng", "染": "ran", "柱": "zhu", "树": "shu", "梅": "mei",
    "梦": "meng", "椛": "hua", "槃": "pan", "槛": "jian", "死": "si",
    "殁": "mo", "殿": "dian", "水": "shui", "池": "chi", "沃": "wo",
    "沉": "chen", "沙": "sha", "河": "he", "泉": "quan", "洛": "luo",
    "洲": "zhou", "流": "liu", "海": "hai", "涅": "nie", "深": "shen",
    "清": "qing", "潮": "chao", "瀑": "pu", "火": "huo", "灵": "ling",
    "点": "dian", "烟": "yan", "煅": "duan", "熔": "rong", "片": "pian",
    "物": "wu", "狮": "shi", "狼": "lang", "玉": "yu", "琉": "liu",
    "璃": "li", "界": "jie", "痕": "hen", "白": "bai", "的": "de",
    "盖": "gai", "眠": "mian", "瞭": "liao", "知": "zhi", "石": "shi",
    "砂": "sha", "砌": "qi", "研": "yan", "碑": "bei", "碣": "jie",
    "示": "shi", "社": "she", "神": "shen", "祭": "ji", "祸": "huo",
    "离": "li", "稻": "dao", "究": "jiu", "空": "kong", "童": "tong",
    "笼": "long", "纬": "wei", "纳": "na", "终": "zhong", "经": "jing",
    "结": "jie", "绿": "lv", "缘": "yuan", "缚": "fu", "罪": "zui",
    "群": "qun", "羽": "yu", "者": "zhe", "聚": "ju", "肇": "zhao",
    "脊": "ji", "腾": "teng", "至": "zhi", "舟": "zhou", "船": "chuan",
    "色": "se", "芬": "fen", "花": "hua", "苍": "cang", "苗": "miao",
    "茂": "mao", "茔": "ying", "荆": "jing", "草": "cao", "荒": "huang",
    "荣": "rong", "荫": "yin", "莉": "li", "莱": "lai", "菫": "jin",
    "营": "ying", "落": "luo", "蒙": "meng", "蔽": "bi", "蕴": "yun",
    "藏": "zang", "虚": "xu", "虹": "hong", "蛇": "she", "蛋": "dan",
    "蜃": "shen", "螺": "luo", "褪": "tui", "西": "xi", "觉": "jue",
    "解": "jie", "记": "ji", "识": "shi", "谜": "mi", "谷": "gu",
    "质": "zhi", "赝": "yan", "赤": "chi", "轮": "lun", "进": "jin",
    "连": "lian", "迹": "ji", "送": "song", "逆": "ni", "通": "tong",
    "造": "zao", "道": "dao", "遗": "yi", "邦": "bang", "邸": "di",
    "都": "du", "金": "jin", "铁": "tie", "铃": "ling", "铭": "ming",
    "锚": "mao", "长": "chang", "门": "men", "阁": "ge", "阵": "zhen",
    "陆": "lu", "隐": "yin", "雨": "yu", "零": "ling", "雷": "lei",
    "霄": "xiao", "震": "zhen", "霜": "shuang", "顶": "ding", "须": "xu",
    "风": "feng", "馆": "guan", "香": "xiang", "验": "yan", "魔": "mo",
    "鸣": "ming", "鹰": "ying", "黄": "huang", "黑": "hei", "黯": "an",
    "龙": "long",
}

#: 常见错别字 → 正确字（沿用 ``mapdata.normalize_name`` 里那两条，保留一致）。
_TYPOS: tuple[tuple[str, str], ...] = (("七天神仙", "七天神像"), ("安绕", "安饶"))

#: 语音识别 / 手打常见的同音（近音）混淆组。**键是标准字，值是它的变体**。
#:
#: ★ 为什么敢把它们当同一个
#:   这些字在**同一个传送点名里**几乎不会同时出现（``风神像`` 和 ``封神像``
#:   指同一个地方），把它们折叠成等价，代价远小于让用户搜不到。
#:   折叠只用于**同音兜底**这一路打分（0.78），低于编辑距离和相似度，
#:   不会盖掉真正的精确命中。
_HOMOPHONE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("风", "封", "枫", "疯"),          # feng
    ("蒙", "梦", "猛", "盟"),          # meng
    ("离", "璃", "里", "黎"),          # li
    ("绝", "决", "角"),                # jue / jiao
    ("七", "其", "器", "起", "漆"),    # qi
    ("岩", "颜", "沿"),                # yan
    ("神", "伸", "深", "参"),          # shen
    ("祠", "词", "慈"),                # ci
    ("域", "玉", "遇", "预"),          # yu
    ("层", "曾", "岑"),                # ceng
    ("泉", "全", "圈"),                # quan
    ("岛", "倒", "导", "盗"),          # dao
    ("庙", "秒", "描"),                # miao
    ("桥", "乔", "侨", "瞧"),          # qiao
    ("塔", "他", "她", "踏"),          # ta
    ("庙", "庙"),                     # 保留组语义（占位，防止空组）
)

_HOMOPHONE_CANON: dict[str, str] = {}
for _grp in _HOMOPHONE_GROUPS:
    _c = _grp[0]
    for _v in _grp:
        _HOMOPHONE_CANON[_v] = _c

#: 国家/地区名 -> 该国家的传送点。用于「地区感知」加分。
_COUNTRIES: tuple[str, ...] = (
    "蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "至冬",
    "挪德卡莱", "空之神殿", "渊下宫", "层岩巨渊", "旧日之海",
    "远古圣山", "霜月",
)

#: 常见中文数字，用于「第八层」→「第8层」这类语音识别归一化。
_CN_DIGITS = {
    "零": "0", "〇": "0", "一": "1", "壹": "1", "二": "2", "两": "2", "贰": "2",
    "三": "3", "叁": "3", "四": "4", "肆": "4", "五": "5", "伍": "5",
    "六": "6", "陆": "6", "七": "7", "柒": "7", "八": "8", "捌": "8",
    "九": "9", "玖": "9", "十": "10", "拾": "10",
}

_SPACES = re.compile(r"[\s\u3000]+")
#: 要**删掉**的标点/连接符。
#:
#: ⚠️ 必须含 ASCII ``-``（U+002D）：NFKC 会把全角 ``－``(U+FF0D) 转成 ASCII ``-``，
#:    只列全角的话，转完之后反而没人删它 —— 「七天神像-风」与「七天神像风」
#:    就比不上，拼音维度被迫顶上来（实测会把 1.0 的精确命中降成 0.95 的拼音命中）。
_PUNCT_CHARS = "，、－—-_·・．,.;:：;!?！？\"'“”‘’()（）【】[]{}<>《》"


# =============================================================================
# 归一化
# =============================================================================

def normalize_query(text: str) -> str:
    """把用户/语音输入收拾成可比对的串。

    依次做：

    1. NFKC —— 全角转半角；
    2. 去空白（含全角空格）；
    3. **删掉**标点与连接符（「七天神像-风」→「七天神像风」）。

    ★ 第 3 条为什么是**删**而不是换成 ``-``
      :func:`mapdata.normalize_name` 把连接符统一成 ``-`` 再保留，
      因为它服务的是「把两种写法对上」。但搜索要的是**单串比对**，
      留着 ``-`` 会让「七天神像风」和「七天神像风」比不上
      —— 两边都归一化成同一条规则即可。

    ★⚠️ 为什么**不做**中文数字 -> 阿拉伯的整体转换
      最初这里有个 ``{"七": "7", ...}`` 的转换表，结果
      ``normalize_query("七天神像-风")`` 变成了 ``"7天神像-风"`` ——
      **七**不是数字，是专有名词的一部分。936 条数据里所有「七天神像」
      全被毁了，而搜索框本身看不出任何异常。
      现在只认一个窄到不可能伤人的模式：``第`` + 单个中文数字，
      （语音识别的「第8层」->「第八层」）。
      其余一律不动 —— 宁可少认一种语音错误，也不可改坏数据。
    """
    s = unicodedata.normalize("NFKC", str(text or "")).strip()
    s = _SPACES.sub("", s)
    for bad, good in _TYPOS:
        s = s.replace(bad, good)
    s = "".join(ch for ch in s if ch not in _PUNCT_CHARS)
    s = _CN_DIGITS_AFTER_DI.sub(_cn_digit_after_di, s)
    return s


_CN_DIGITS_AFTER_DI = re.compile(r"第([零〇一壹二两贰三叁四肆五伍六陆七柒八捌九玖十拾])")


def _cn_digit_after_di(m: re.Match) -> str:
    """``第八`` -> ``第8``。**只**跟在「第」后面，避免伤到「七天神像」。"""
    return "第" + _CN_DIGITS[m.group(1)]


def canonical_homophone(s: str) -> str:
    """把同音变体折叠成标准写法（``封神像`` -> ``风神像``）。"""
    return "".join(_HOMOPHONE_CANON.get(ch, ch) for ch in s)


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _views(s: str) -> tuple[str, str]:
    """-> ``(拼音首字母串, 拼音全拼串)``。**覆盖不全时返回 ``("", "")``。

    ★ 为什么覆盖不全就整体作废，而不是"能算几个算几个"
      初版是遇到表外字就跳过，结果
      ``to_initials("特别温暖的地方") == to_initials("zzzz不存在的地名") == "dd"``
      —— 两句毫不相干的话塌成同一个串，然后被 ``ini == ini`` 判成
      **0.95 分的强命中**。这是比匹配不到严重得多的错误：
      它会给用户一个看起来很确定、实际完全错误的答案。
      所以表外字一律让整条拼音维度作废，转由其它维度打分。
    """
    initials: list[str] = []
    full: list[str] = []
    for ch in s:
        if _is_cjk(ch):
            py = _PINYIN.get(ch)
            if py is None:
                return "", ""
            initials.append(py[0])
            full.append(py)
        elif ch.isascii() and ch.isalpha():
            # 已经是拉丁字母 —— 当成用户直接打的拼音，原样参与比对。
            # 这样 ``qtsx`` 的首字母视图就是 ``qtsx`` 本身。
            initials.append(ch.lower())
            full.append(ch.lower())
        # 标点、数字：跳过（数字不参与拼音比对）。
    return "".join(initials), "".join(full)


def to_pinyin(s: str) -> str:
    """无声调全拼。表外字符会**丢弃**该字；覆盖不全见 :func:`_views`。

    优先用 :func:`_views` 的第二个返回值 —— 它在覆盖不全时会明确作废，
    本函数保留给"只要尽力转出来"的场合（如调试打印）。
    """
    return "".join(_PINYIN.get(ch, ch) for ch in s)


def to_initials(s: str) -> str:
    """拼音首字母（``七天神像`` -> ``qtsx``）。覆盖不全时返回 ``""``。

    ⚠️ 返回空串是**有意义的信号**，不是"没查到"：调用方必须把空串
    当作"这一维度不可用"跳过，而不是拿去和别的空串比。
    """
    return _views(s)[0]


# =============================================================================
# 打分
# =============================================================================

def _edit_distance(a: str, b: str, cap: int = EDIT_DISTANCE_CAP) -> int:
    """带上限的编辑距离；超过 ``cap`` 早退返回 ``cap+1``。

    与插件侧 ``teleport_ocr._edit_distance`` 同实现、同阈值。
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1,
                           cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _bag_match(query: str, cand: str) -> tuple[float, str] | None:
    """**乱序/漏字**匹配：查询的每个字都在候选里出现过就算命中。

    ★ 为什么需要它
      ``风神`` 想找 ``七天神像-风``，但「风」在第 5 位、「神」在第 3 位 ——
      **顺序是反的**，子串匹配和编辑距离全都接不住（相似度只有 0.57）。
      语音识别漏字也会产生同样的形态（``风像`` vs ``风神像``）。
      所以再补一个"字袋"维度：**顺序无关**，只要字都到齐。

    分数按**候选被覆盖的比例**缩放：查询越能解释候选，分数越高。
    ``风神`` 打 ``七天神像风`` 只能解释 2/5 -> 分低；打 ``风神`` 自己 2/2 -> 高。

    :returns: ``None`` 表示查询有字不在候选里（即不是子集关系）。
    """
    if len(query) < 2:
        # 单字太弱：的/风/神 这种字在任何长地名里都有，会把整个列表污染。
        return None
    qs, cs = set(query), set(cand)
    if not qs.issubset(cs):
        return None
    coverage = len(qs) / float(len(cs))
    return 0.60 + 0.18 * coverage, "bag"


def _ladder(a: str, b: str, *, allow_homophone: bool,
            pinyin: bool = False) -> tuple[float, str] | None:
    """单一维度（一种书写形式对另一种）的打分阶梯。

    :param pinyin:  走的是拼音维度。

    ★ 拼音维度为什么只认**前缀**、且要够长
      初版拼音维度用双向子串，结果 ``"fs"``（风神）被判定为
      ``"jsxfsl"``（聚所西风戍垒）的子串 —— 两个字母在长串里偶然相邻，
      就拿到了 0.85 的强命中。用户会得到一个看起来很确定、完全错误的答案。
      真实场景里拼音只可能是**开头几个字母**（``qtsx`` -> ``qtsxf``），
      所以拼音维度只做前缀匹配，且两边至少 2 个字母。
    """
    if not a or not b:
        return None
    if a == b:
        return 1.0, "exact"
    if pinyin:
        if min(len(a), len(b)) >= 2 and (b.startswith(a) or a.startswith(b)):
            return 0.85, "prefix"
    else:
        if a in b or b in a:
            return 0.85, "substring"
        hit = _bag_match(a, b)
        if hit is not None:
            return hit
    d = _edit_distance(a, b)
    # ★ 必须按**较短串**的长度归一化，不能只看绝对编辑距离
    #   实测 ``蒙度``（语音把"蒙德"听成"梦度"）：
    #     vs ``蒙德``    改 1 字 -> 正确
    #     vs ``七天神像风`` 改 2 字 -> 全错
    #   但两者绝对编辑距离都 <= 2、初版给的分几乎一样，结果"正确答案"被
    #   长度更长的错误候选挤掉。按 short 归一化后：0.75 vs 0.55，蒙德胜出。
    short = min(len(a), len(b))
    if pinyin and short < PYMIN_EDIT_LEN:
        # ★ 拼音首字母天然很短，"2 个字母差 1 个"没有任何判别力。
        #   实测 ``风像`` 的首字母 "fx" 与 **枫丹** 的 "fd" 编辑距离 1，
        #   于是"风像"在 0.75 上匹配上了枫丹的神像 —— 纯噪声。
        #   拼音维度要动用编辑距离，至少得有 4 个字母。
        return None
    if d <= EDIT_DISTANCE_CAP and d <= max(1, short // 2):
        return 0.55 + 0.40 * (1.0 - d / float(short)), "edit"
    s = _sim(a, b)
    if s >= SIMILARITY_FLOOR:
        return 0.50 + 0.35 * (s - SIMILARITY_FLOOR) / (1.0 - SIMILARITY_FLOOR), "similar"
    if allow_homophone and canonical_homophone(a) == canonical_homophone(b):
        return 0.78, "homophone"
    return None


def _score_pair(query: str, cand: str) -> tuple[float, str] | None:
    """单个「查询 × 候选串」打分。返回 ``(分数, 理由)``，不匹配返回 None。

    ★ 为什么要在**三个维度**上分别跑一遍阶梯
      用户输入和地名的"书写形式"可以完全不同：用户打 ``qtsx``，
      地名是 ``七天神像``。只比中文字符串永远比不上；只比拼音又盖不住
      错别字。所以对每一侧各算三种视图，**取所有组合里的最高分**：

      ==========  =====================  =====================
      视图          ``七天神像-风``         ``qtsx``
      ==========  =====================  =====================
      中文          ``七天神像风``          ``qtsx``（原样）
      拼音首字母    ``qtsxf``               ``qtsx``
      拼音全拼      ``qitianshenxiangfeng`` ``qtsx``
      ==========  =====================  =====================

      拼音视图覆盖不全时是空串（见 :func:`_views`），空串直接跳过，
      **绝不会**和另一边的空串"相等"。
    """
    if not query or not cand:
        return None

    q_zh, q_in, q_py = query, *_views(query)
    c_zh, c_in, c_py = cand, *_views(cand)

    best: tuple[float, str] | None = None
    for a, b, tag in ((q_zh, c_zh, ""),
                      (q_in, c_in, "pinyin-initials"),
                      (q_py, c_py, "pinyin-full")):
        is_py = bool(tag)
        if is_py and (not a or not b):
            # 覆盖不全 -> 这一维度不可用，跳过（绝不让空串和空串"相等"）
            continue
        hit = _ladder(a, b, allow_homophone=not is_py, pinyin=is_py)
        if hit is None:
            continue
        if is_py:
            # 拼音命中略低于中文字面命中：用户直接打中文字的把握更大。
            hit = (min(0.95, hit[0]),
                   "initials" if "initials" in tag else "pinyin")
        if best is None or hit[0] > best[0]:
            best = hit
    return best


# =============================================================================
# 结果
# =============================================================================

@dataclass(frozen=True)
class Candidate:
    """一条搜索结果。

    :param point:    对应的 :class:`mapdata.TeleportPoint`。
    :param score:    0~1，越高越像。
    :param reasons:  命中理由（可读），排序稳定时用它做 tie-break。
    :param matched:  实际命中的那个候选串，便于 UI 显示「你搜的落在哪」。
    :param region_boost: 地区感知加分（0 或正数）。
    """

    point: object
    score: float
    reasons: tuple[str, ...]
    matched: str
    region_boost: float = 0.0

    @property
    def label(self) -> str:
        """UI 主标签：``名称 · 地区``。

        936 个点里 777 个都叫「传送锚点」，只显示 name 等于没显示 ——
        所以**必须**把 areas 拼进来，这也是 ``(name, areas)`` 联合键的意义。
        """
        p = self.point
        area = "、".join(getattr(p, "areas", ()) or ()) or "—"
        return f"{getattr(p, 'name', '')} · {area}"

    @property
    def subtitle(self) -> str:
        """UI 副标签：国家 + 类型 + id。"""
        p = self.point
        return (f"{getattr(p, 'country', '') or '—'} / "
                f"{getattr(p, 'type', '')} / #{getattr(p, 'id', 0)}")


def _candidate_strings(point) -> tuple[str, ...]:
    """一个传送点对外暴露的**全部**可搜字符串。

    与插件侧 ``teleport_ocr._score_pair`` 的候选集一致：``{name}`` /
    ``{name}-{area}`` / ``{area}``。这里额外加 ``country`` 与
    ``{name}-{country}``，因为 Console 侧用户更习惯说「蒙德的神像」而不是
    具体地名。
    """
    name = normalize_query(getattr(point, "name", ""))
    country = normalize_query(getattr(point, "country", ""))
    areas = [normalize_query(a) for a in (getattr(point, "areas", ()) or ())]
    out = [name]
    out += [f"{name}-{a}" for a in areas]
    out += areas
    if country:
        out.append(country)
        out.append(f"{name}-{country}")
    seen: set[str] = set()
    uniq = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return tuple(uniq)


def _detect_region(query: str) -> str:
    """查询里提到的国家/地区名（没有则空串）。

    「蒙德 七天神像」-> ``蒙德``。用于地区感知加分。
    """
    for c in _COUNTRIES:
        if normalize_query(c) and normalize_query(c) in query:
            return c
    return ""


@lru_cache(maxsize=4)
def _search_index(points_key: tuple, points: Sequence) -> tuple[tuple, ...]:
    """把传送点表预处理成 ``(point, 候选串, 字符集, 拼音首字母)``。

    ★ 为什么必须缓存
      :func:`_candidate_strings` 每个点要跑 3~5 次 :func:`normalize_query`
      （含 NFKC + 两次 replace + 正则），936 个点就是 ~4000 次正则。
      搜索框是**每敲一个键就搜一次**，不缓存的话单次 ~130ms ——
      打字过程中会明显卡顿。数据是不变的（``load_teleport_points``
      本身也是 ``lru_cache``），预处理一次即可。

    同时把**字符集**和**首字母**也预计算出来，供 :func:`search_teleport`
    做快速筛除 —— 936 个点绝大多数与查询无关，逐个算编辑距离纯属浪费。

    :param points_key: 必须是可哈希的 points 标识（``tuple(id)``）。
    """
    idx = []
    for p in points:
        cands = _candidate_strings(p)
        if not cands:
            continue
        views = [_views(c) for c in cands]
        idx.append((
            p, cands,
            frozenset("".join(cands)),
            tuple(v[0] for v in views if v[0]),      # 首字母视图
            tuple(v[1] for v in views if v[1]),      # 全拼视图
        ))
    return tuple(idx)


def search_teleport(query: str, *, limit: int = 10,
                    points: Sequence | None = None) -> list[Candidate]:
    """模糊搜索传送点，按分数降序返回前 ``limit`` 个。

    :param query:  任意文本 —— 手打、语音识别结果、拼音都行。
    :param limit:  最多返回几条（UI 下拉建议 5~10）。
    :param points: 传送点表；``None`` 时用 :func:`mapdata.load_teleport_points`。
    :returns: 分数 >= :data:`SCORE_FLOOR` 的候选，空查询返回空列表。
    """
    if points is None:
        from .mapdata import load_teleport_points
        points = load_teleport_points()

    q = normalize_query(query)
    if not q or not points:
        return []

    q_canon = canonical_homophone(q)
    region = _detect_region(q)

    # 快速筛除：汉字查询要求至少共享一个字；纯拉丁（拼音）查询走首字母前缀。
    q_chars = frozenset(q) | frozenset(q_canon)
    q_ini = _views(q)[0]
    ascii_query = q.isascii()
    index = _search_index(tuple(getattr(p, "id", 0) for p in points), points)

    out: list[Candidate] = []
    for p, cands, p_chars, p_inis, p_pys in index:
        if ascii_query:
            # 拼音查询：只要**任一**候选串的首字母**或全拼**以前缀对上就留下。
            #
            # ★ 两个坑都踩过
            #   1. 聚合成一个值再比（初版用 min）——七天神像-冰 的候选串里
            #      有「gsby」（古兽冰原）比「qtsxb」小，正确候选被否掉。
            #   2. 只比首字母 —— 用户打全拼 ``qitianshenxiang`` 时，
            #      首字母视图是 "qtsxb"，永远对不上，于是**一个结果都没有**。
            #
            # ★ 为什么预筛只认**前缀**而不认编辑距离/相似度
            #   预筛的目的是"把 936 条里明显无关的砍掉"，砍错了就是
            #   正确答案根本不出现 —— 比打分偏低严重得多。
            #   所以这里必须和 :func:`_ladder` 的拼音维度保持同样宽松。
            ok = False
            for pool in (p_inis, p_pys):
                for v in pool:
                    if v.startswith(q_ini) or q_ini.startswith(v):
                        ok = True
                        break
                if ok:
                    break
            if q_ini and not ok:
                continue
        elif not (q_chars & p_chars):
            continue
        best = (0.0, (), "")
        for cand in cands:
            hits = [_score_pair(q, cand)]
            if q_canon != q:
                # 同音兜底：**无条件**再试一次折叠后的查询，并取两者更高分。
                #
                # ★ 为什么不能"首次没命中才重试"
                #   ``梦度``（语音把"蒙德"听成"梦度"）对 ``须弥`` 能走
                #   edit 维度命中 0.68，所以"已经有命中"了；可真正该答的
                #   ``蒙德`` 要把 梦 折成 蒙 才是 1.0。
                #   初版写成 `if hit is None and q_canon != q` ——
                #   有了别的弱命中就再也没机会试折叠，结果返回的是须弥。
                #   弱命中挡不住正确答案，必须**都算，取大**。
                hits.append(_score_pair(q_canon, cand))
            for hit in hits:
                if hit is None:
                    continue
                score, reason = hit
                if q_canon != q and hit is not None and reason != "exact" \
                        and canonical_homophone(q) == canonical_homophone(cand):
                    reason = f"{reason}+homophone"
                if score > best[0]:
                    best = (score, (reason,), cand)
        if best[0] < SCORE_FLOOR:
            continue

        score, reasons, matched = best
        boost = 0.0
        if region and getattr(p, "country", "") == region:
            boost = 0.10
        out.append(Candidate(point=p, score=min(1.0, score + boost),
                            reasons=reasons, matched=matched,
                            region_boost=boost))

    # 排序：分数降序 -> 分数相同则 name 升序，保证同分结果顺序稳定可预期。
    out.sort(key=lambda c: (-c.score, getattr(c.point, "name", ""),
                            getattr(c.point, "id", 0)))
    return out[:limit]


# =============================================================================
# NEKO 交互（B3）
# =============================================================================

def needs_disambiguation(candidates: Sequence[Candidate]) -> bool:
    """候选是不是「分不清」，该问用户了。

    两条触发线（任务书 B3）：

    * top1 分数 < :data:`AMBIGUITY_TOP_SCORE`（0.60）—— 匹配得本身不好；
    * top1 与 top2 差 < :data:`AMBIGUITY_SCORE_GAP`（0.15）—— 难兄难弟。

    只有 1 条候选时**不**问 —— 没得选，问了也是废话。
    """
    if not candidates:
        return False
    top = candidates[0]
    if top.score < AMBIGUITY_TOP_SCORE:
        return True
    if len(candidates) < 2:
        return False
    return (top.score - candidates[1].score) < AMBIGUITY_SCORE_GAP


def build_disambiguation_question(candidates: Sequence[Candidate]) -> str:
    """生成给用户/NEKO 看的一句话问句。

    ★ 为什么本轮只落成**字符串**而不是接通道
      Console 侧现有 IPC 只有两个方向：``console_bridge.json``
      （Console→插件）、``console_commands.jsonl``（插件→Console）。
      **没有 Console→NEKO 对话的反向通道**（见 ``bridge.py`` 的模块 docstring）。
      按任务书约定，本轮把这条接缝留在这里：等反向通道有了，
      把这个字符串塞进去即可，调用方一行都不用改。

    >>> build_disambiguation_question(cs)
    '你是想去【七天神像-风 · 苍风高地】还是【七天神像-风 · 风啸山坡】？'
    """
    if not candidates:
        return ""
    head = candidates[0].label
    rest = [c.label for c in candidates[1:4]]
    if not rest:
        return f"你是想去【{head}】吗？"
    tail = "、还是".join(f"【{r}】" for r in rest)
    return f"你是想去【{head}】还是{tail}？"
