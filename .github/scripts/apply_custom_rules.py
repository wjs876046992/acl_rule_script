#!/usr/bin/env python3
"""把 .github/custom-rules/*.domains 里的自定义域名写入各平台的规则文件。

每次上游同步（硬重置）之后运行，保证自定义域名始终存在。
脚本是幂等的：已存在的条目不会重复写入，被上游 DOMAIN-SUFFIX 覆盖的会跳过。

同名的 `*.exclude` 文件（如果存在）列出要从该规则集中**删除**的域名，
用于把上游默认直连的站点点名剔除（例如某个 PT 站需要走代理）。
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CUSTOM_DIR = os.path.join(REPO, ".github", "custom-rules")

RULE_RE = re.compile(r"^(?P<indent>\s*)(?P<dash>- )?(?P<type>[A-Z][A-Z0-9-]*),(?P<body>.+?)(?P<nl>\n)?$")

# 域名型规则，以及它们的“后缀”兄弟类型（用于判断是否已被覆盖）
DOMAIN_TYPES = {"DOMAIN", "HOST"}
SUFFIX_TYPES = {"DOMAIN-SUFFIX", "HOST-SUFFIX"}
KEYWORD_TYPES = {"DOMAIN-KEYWORD", "HOST-KEYWORD"}
IP_TYPES = {"IP-CIDR", "IP-CIDR6"}

ORDER = ["DOMAIN", "HOST", "DOMAIN-SUFFIX", "HOST-SUFFIX",
         "DOMAIN-KEYWORD", "HOST-KEYWORD", "IP-CIDR", "IP-CIDR6"]

HEADER_RE = re.compile(r"^# (?P<key>[A-Z][A-Z0-9-]*): (?P<value>.+)$")


def read_domains(path):
    """读取自定义规则，返回 [(kind, name)]，kind 为 'domain', 'suffix' 或 'keyword'。

    支持写法：
      example.com                -> DOMAIN / HOST（默认）
      DOMAIN-SUFFIX,example.com  -> DOMAIN-SUFFIX / HOST-SUFFIX
      DOMAIN-KEYWORD,example     -> DOMAIN-KEYWORD / HOST-KEYWORD
    """
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "," in line:
                kind, _, name = line.partition(",")
                kind = kind.strip().upper()
                name = name.strip().lower()
                if kind in SUFFIX_TYPES:
                    out.append(("suffix", name))
                elif kind in DOMAIN_TYPES:
                    out.append(("domain", name))
                elif kind in KEYWORD_TYPES:
                    out.append(("keyword", name))
                else:
                    raise ValueError("不支持的规则类型: %r" % line)
            else:
                out.append(("domain", line.lower()))
    return out


def parse(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    header, rules, others = [], [], []
    for line in lines:
        m = RULE_RE.match(line)
        if m and m.group("type") in ORDER:
            rules.append({"indent": m.group("indent"),
                          "dash": m.group("dash") or "",
                          "type": m.group("type"),
                          "body": m.group("body")})
        elif HEADER_RE.match(line):
            header.append(line)
        else:
            others.append(line)

    return header, rules, others


def read_exclude(path):
    """读取 `*.exclude`，返回需要从规则集中删除的域名集合（小写、已去重）。

    写法与 `.domains` 不同，这里只看**域名**：`nicept.net` 与
    `DOMAIN-SUFFIX,nicept.net` 等价，都会删掉该域名及其子域的条目。
    """
    if not os.path.exists(path):
        return set()
    names = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            _, _, name = line.rpartition(",")
            names.add(name.strip().lower())
    return names


def excluded(value, excludes):
    """规则值是否命中排除名单：同域，或是被排除域名的子域。"""
    value = value.lower()
    return any(value == e or value.endswith("." + e) for e in excludes)


def split_value(rule):
    """返回 (value, tail)；tail 形如 ',PrivateTracker' 或 ''。"""
    body = rule["body"]
    if "," in body:
        value, tail = body.split(",", 1)
        return value, "," + tail
    return body, ""


def build_entry(template, type_name, value):
    """按已有规则的缩进/前缀风格，生成一条新规则文本。"""
    _, tail = split_value(template)
    return "{indent}{dash}{type},{value}{tail}".format(
        indent=template["indent"], dash=template["dash"],
        type=type_name, value=value, tail=tail)


def update_header(header, rules, extra_total=0):
    counts = {}
    for r in rules:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    total = len(rules) + extra_total
    stamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

    out = []
    for line in header:
        m = HEADER_RE.match(line)
        if not m:
            out.append(line)
            continue
        key = m.group("key")
        if key == "UPDATED":
            out.append("# UPDATED: %s" % stamp)
        elif key == "TOTAL":
            out.append("# TOTAL: %d" % total)
        elif key in counts or key in ORDER or key in {"HOST"}:
            out.append("# %s: %d" % (key, counts.get(key, 0)))
        else:
            out.append(line)
    return out


def apply_file(path, domains, excludes=frozenset()):
    if not os.path.exists(path):
        return None
    header, rules, others = parse(path)

    # 各类型各取一个模板行，用于沿用缩进与尾部字段（如 QuantumultX 的 ",PrivateTracker"）
    templates = {}
    for r in rules:
        templates.setdefault(r["type"], r)
    if not templates:
        return None

    # PROCESS-NAME / USER-AGENT 等不在 ORDER 里的类型不参与编辑，但仍计入 TOTAL
    extra_total = sum(1 for line in others if RULE_RE.match(line))

    # 先剔除点名要删除的域名，其余逻辑照常（新增条目也不会再被排除掉）
    removed = 0
    if excludes:
        kept = [r for r in rules if not excluded(split_value(r)[0], excludes)]
        removed = len(rules) - len(kept)
        rules = kept
        if not rules:
            # 全被删空时保留原样，避免生成没有模板可依的空规则集
            header, rules, others = parse(path)
            removed = 0

    def host_variant(surge_type):
        """把 DOMAIN/DOMAIN-SUFFIX/DOMAIN-KEYWORD 映射到该文件实际使用的 HOST 变体。"""
        if surge_type == "DOMAIN":
            return "HOST" if "HOST" in templates else "DOMAIN"
        elif surge_type == "DOMAIN-SUFFIX":
            return "HOST-SUFFIX" if "HOST-SUFFIX" in templates else "DOMAIN-SUFFIX"
        elif surge_type == "DOMAIN-KEYWORD":
            return "HOST-KEYWORD" if "HOST-KEYWORD" in templates else "DOMAIN-KEYWORD"
        return surge_type

    existing_domains = {split_value(r)[0].lower()
                       for r in rules if r["type"] in DOMAIN_TYPES}
    existing_suffixes = {split_value(r)[0].lower()
                         for r in rules if r["type"] in SUFFIX_TYPES}
    existing_keywords = {split_value(r)[0].lower()
                         for r in rules if r["type"] in KEYWORD_TYPES}

    def covered_by_suffix(name):
        return any(name == s or name.endswith("." + s) for s in existing_suffixes)

    to_add = []
    pending_suffixes = []
    for kind, name in domains:
        if kind == "keyword":
            if name in existing_keywords:
                continue
            to_add.append(("keyword", name))
        elif kind == "suffix":
            # 后缀规则会覆盖其下所有子域，因此若已存在同后缀则跳过
            if name in existing_suffixes:
                continue
            if name not in pending_suffixes:
                pending_suffixes.append(name)
                to_add.append(("suffix", name))
        else:
            if name in existing_domains or covered_by_suffix(name):
                continue
            # 本次新增的后缀若已覆盖该域名，则无需再加 DOMAIN
            if any(name == s or name.endswith("." + s) for s in pending_suffixes):
                continue
            to_add.append(("domain", name))

    if not to_add and not removed:
        return 0, 0

    new_rules = []
    for kind, name in to_add:
        if kind == "domain":
            surge_name = "DOMAIN"
        elif kind == "suffix":
            surge_name = "DOMAIN-SUFFIX"
        elif kind == "keyword":
            surge_name = "DOMAIN-KEYWORD"
        type_name = host_variant(surge_name)
        template = templates.get(type_name)
        if not template:
            # 若文件中尚无该类型的规则，借用任意已有模板的缩进和尾部
            any_tmpl = next(iter(templates.values()))
            template = {"indent": any_tmpl["indent"], "dash": any_tmpl["dash"],
                        "type": type_name, "body": ""}
        _, tail = split_value(template)
        new_rules.append({"indent": template["indent"], "dash": template["dash"],
                          "type": type_name, "body": name + tail})
    merged = rules + new_rules

    # 新增后缀后，原有的、已被该后缀覆盖的 DOMAIN 条目就是冗余的，删掉
    if pending_suffixes:
        merged = [r for r in merged
                  if not (r["type"] in DOMAIN_TYPES
                          and any(split_value(r)[0].lower() == s
                                  or split_value(r)[0].lower().endswith("." + s)
                                  for s in pending_suffixes))]

    # 保持文件原有的类型分组顺序，组内按值排序
    seen_order = []
    for r in merged:
        if r["type"] not in seen_order:
            seen_order.append(r["type"])
    for t in ORDER:
        if t in seen_order:
            continue
        if any(r["type"] == t for r in merged):
            seen_order.append(t)

    groups = {}
    for r in merged:
        value, _ = split_value(r)
        groups.setdefault(r["type"], []).append((value.lower(), r))

    body_lines = []
    for type_name in seen_order:
        for _, r in sorted(groups[type_name], key=lambda item: item[0]):
            body_lines.append(build_entry(r, r["type"], split_value(r)[0]))

    header = update_header(header, merged, extra_total)

    # others 中保留非规则、非头部的原样内容（含空行与 payload: 等）
    text = "\n".join(header) + "\n" + "\n".join(body_lines)
    if others:
        trimmed = [l for l in others if l.strip()]
        # payload:/其它标记行若存在于规则之前，需要还原到头部之后
        prefix = [l for l in others if l.strip() in ("payload:",)]
        rest = [l for l in trimmed if l.strip() not in ("payload:",)]
        if prefix:
            text = "\n".join(header) + "\n" + "\n".join(prefix) + "\n" + "\n".join(body_lines)
        for line in rest:
            text += "\n" + line
    text = text.rstrip("\n") + "\n"

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(to_add), removed


def apply_readme(path, source_file):
    """让 README 的规则统计与规则文件保持一致。"""
    if not os.path.exists(path) or not os.path.exists(source_file):
        return False
    _, rules, others = parse(source_file)
    if not rules:
        return False

    counts = {}
    for r in rules:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    # PROCESS-NAME / USER-AGENT 等不在 ORDER 里的类型，README 也要统计
    total = len(rules)
    for line in others:
        m = RULE_RE.match(line)
        if m and m.group("type") not in ORDER:
            counts[m.group("type")] = counts.get(m.group("type"), 0) + 1
            total += 1

    stamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    out, counts_changed, stamp_stale = [], False, False
    for line in lines:
        m = re.match(r"^(\|\s*)([A-Z][A-Z0-9-]*)(\s*\|\s*)(\d+)(\s*\|)\s*$", line)
        if m and (m.group(2) in counts or m.group(2) == "TOTAL"):
            want = total if m.group(2) == "TOTAL" else counts.get(m.group(2), 0)
            if int(m.group(4)) != want:
                line = "%s%s%s%d%s" % (m.group(1), m.group(2), m.group(3), want, m.group(5))
                counts_changed = True
        elif line.startswith("最后更新时间："):
            # 仅在统计变化时刷新，避免每次运行都产生无意义的 diff
            stamp_stale = line != "最后更新时间：%s" % stamp
            if not stamp_stale:
                stamp_stale = False
            out.append(line)
            continue
        out.append(line)

    if counts_changed and stamp_stale:
        out = ["最后更新时间：%s" % stamp if l.startswith("最后更新时间：") else l
               for l in out]

    if counts_changed:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out))
    return counts_changed


def main():
    # `.domains` 负责新增，`.exclude` 负责删除；只写 `.exclude` 也是合法的
    # （例如只想把某个站从上游的 Direct 规则集里剔掉）。
    names = set()
    for f in os.listdir(CUSTOM_DIR):
        if f.endswith(".domains"):
            names.add(f[: -len(".domains")])
        elif f.endswith(".exclude"):
            names.add(f[: -len(".exclude")])

    total = 0
    for ruleset in sorted(names):
        domain_path = os.path.join(CUSTOM_DIR, ruleset + ".domains")
        domains = read_domains(domain_path) if os.path.exists(domain_path) else []
        excludes = read_exclude(os.path.join(CUSTOM_DIR, ruleset + ".exclude"))
        print("== %s: %d 个自定义规则%s" % (
            ruleset, len(domains),
            "，%d 个排除域名" % len(excludes) if excludes else ""))

        targets = []
        for platform in ("Surge", "Loon", "Shadowrocket", "QuantumultX", "Clash"):
            base = os.path.join(REPO, "rule", platform, ruleset)
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                if name.endswith((".list", ".yaml")):
                    targets.append(os.path.join(base, name))

        for path in targets:
            result = apply_file(path, domains, excludes)
            if result is None:
                continue
            added, removed = result
            if not added and not removed:
                continue
            rel = os.path.relpath(path, REPO)
            print("   %-58s 新增 %d，删除 %d" % (rel, added, removed))
            total += added - removed

            readme = os.path.join(os.path.dirname(path), "README.md")
            if apply_readme(readme, path):
                print("   %-58s 已更新统计" % os.path.relpath(readme, REPO))
    print("== 合计净增 %d 条" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
