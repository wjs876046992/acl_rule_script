#!/usr/bin/env python3
"""把 .github/custom-rules/*.domains 里的自定义域名写入各平台的规则文件。

每次上游同步（硬重置）之后运行，保证自定义域名始终存在。
脚本是幂等的：已存在的条目不会重复写入，被上游 DOMAIN-SUFFIX 覆盖的会跳过。
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
    """读取自定义规则，返回 [(kind, name)]，kind 为 'domain' 或 'suffix'。

    支持两种写法：
      example.com                -> DOMAIN / HOST（默认）
      DOMAIN-SUFFIX,example.com  -> DOMAIN-SUFFIX / HOST-SUFFIX
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


def update_header(header, rules):
    counts = {}
    for r in rules:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    total = len(rules)
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


def apply_file(path, domains):
    if not os.path.exists(path):
        return None
    header, rules, others = parse(path)

    # 各类型各取一个模板行，用于沿用缩进与尾部字段（如 QuantumultX 的 ",PrivateTracker"）
    templates = {}
    for r in rules:
        templates.setdefault(r["type"], r)
    if not templates:
        return None

    def host_variant(surge_type):
        """把 DOMAIN/DOMAIN-SUFFIX 映射到该文件实际使用的 HOST 变体。"""
        if surge_type == "DOMAIN":
            return "HOST" if "HOST" in templates else "DOMAIN"
        return "HOST-SUFFIX" if "HOST-SUFFIX" in templates else "DOMAIN-SUFFIX"

    existing_domains = {split_value(r)[0].lower()
                       for r in rules if r["type"] in DOMAIN_TYPES}
    existing_suffixes = {split_value(r)[0].lower()
                         for r in rules if r["type"] in SUFFIX_TYPES}

    def covered_by_suffix(name):
        return any(name == s or name.endswith("." + s) for s in existing_suffixes)

    to_add = []
    pending_suffixes = []
    for kind, name in domains:
        if kind == "suffix":
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

    if not to_add:
        return 0

    new_rules = []
    for kind, name in to_add:
        type_name = host_variant("DOMAIN" if kind == "domain" else "DOMAIN-SUFFIX")
        template = templates[type_name]
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

    header = update_header(header, merged)

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
    return len(to_add)


def apply_readme(path, source_file):
    """让 README 的规则统计与规则文件保持一致。"""
    if not os.path.exists(path) or not os.path.exists(source_file):
        return False
    _, rules, _ = parse(source_file)
    if not rules:
        return False

    counts = {}
    for r in rules:
        counts[r["type"]] = counts.get(r["type"], 0) + 1

    stamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    out, counts_changed, stamp_stale = [], False, False
    for line in lines:
        m = re.match(r"^(\|\s*)([A-Z][A-Z0-9-]*)(\s*\|\s*)(\d+)(\s*\|)\s*$", line)
        if m and (m.group(2) in counts or m.group(2) == "TOTAL"):
            want = len(rules) if m.group(2) == "TOTAL" else counts.get(m.group(2), 0)
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
    targets = []
    for platform in ("Surge", "Loon", "Shadowrocket", "QuantumultX", "Clash"):
        base = os.path.join(REPO, "rule", platform, "PrivateTracker")
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name.endswith((".list", ".yaml")):
                targets.append(os.path.join(base, name))

    domain_files = sorted(f for f in os.listdir(CUSTOM_DIR) if f.endswith(".domains"))
    total = 0
    for df in domain_files:
        ruleset = df[: -len(".domains")]
        domains = read_domains(os.path.join(CUSTOM_DIR, df))
        print("== %s: %d 个自定义域名" % (ruleset, len(domains)))
        for path in targets:
            if os.path.basename(os.path.dirname(path)) != ruleset:
                continue
            added = apply_file(path, domains)
            if added is None:
                continue
            rel = os.path.relpath(path, REPO)
            print("   %-58s 新增 %d" % (rel, added))
            total += added

            readme = os.path.join(os.path.dirname(path), "README.md")
            if apply_readme(readme, path):
                print("   %-58s 已更新统计" % os.path.relpath(readme, REPO))
    print("== 合计写入 %d 条" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
