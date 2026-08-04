"""
从 OCR 纯文本中启发式提取岗位结构（视觉 LLM 兜底通道）。

招聘卡片在 OCR 文本里结构不统一，常见两种：
  A 结构：title+salary 同一行（"气象仪器销售经理3000-6000元"），company/location 在下方
  B 结构：title / company / salary 分行，location 在 salary 下方

统一策略：以「含薪资文本的行」为锚点，向上下双向找最近的 title/company/location。
这是兜底通道，质量追求「title/salary_text 尽量准，company/location 尽力」，不要求完美。
"""
from __future__ import annotations

import re

# 薪资模式：如 "3000-6000元" "8000-16000元" "1.5-2.5万" "7000-14000元·13薪" "薪资面议"
_SALARY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[-~—–～]?\s*(\d+(?:\.\d+)?)?\s*(万|k|K|千|元)"
)
_SALARY_FLAT_RE = re.compile(r"(?:薪资)?面议")

_COMPANY_RE = re.compile(r"(公司|集团|股份|有限|研究院|工作室|事务所|中心|科技|实业)")
_LOCATION_RE = re.compile(r"[一-龥]{2,6}·[一-龥]")
_EXP_RE = re.compile(r"(经验|学历|大专|本科|硕士|博士|不限|应届)")
# HR 行特征：名字+职位+活跃状态，不是工作地点
_HR_RE = re.compile(r"(人事经理|人力资源|招聘者|HR|立即沟通|活跃|在线)")
# 明显不是岗位名的行（登录弹窗噪音 / 属性标签 / 单一 logo 字）
_TITLE_BAD_RE = re.compile(
    r"^(国|民营|国企|外资|立即投递|简历|模版|登[录陆]|验证码|手机号|获取验证码|"
    r"已阅读|用户服务|隐私|国家网络|短信|企业客户|政府客户|渠道销售|区域销售|"
    r"\d+-\d+人|我要招人|在线|活跃|人事经理|人力资源)"
)

# 页面 footer/导航噪音：整站链接、热门城市/职位、备案号等，绝不是岗位
_FOOTER_NOISE = re.compile(
    r"(友情链接|热门城市|热门职位|热门公司|职场百科|职场文库|热词榜|简历模板|简历中心|"
    r"新手引导|防骗指南|意见反馈|联系我们|帮助中心|网站地图|常见问题|法律协议|关于我们|"
    r"加入我们|小程序|微信服务号|隐私条款|银行招聘|招聘网|人才网|招聘信息|版权所有|"
    r"无忧工作网|前程无忧|沪ICP|沪公网|ICP|网信算备|电子营业执照|人力资源服务许可证|"
    r"举报|客服热线|销售热线|邮件|Email|工商|所属行业|公司性质|公司规模|工作职能|"
    r"工作类型|工作年限|学历要求|月薪范围|行业领域|清空|温馨提示|[一-龥]{2,4}分公司)"
)

# 短 UI 按钮/标签词（1~4 字，单独成行时不是岗位标题）
_UI_NOISE = re.compile(
    r"^(更多|下载|分享|收藏|投诉|反馈|展开|收起|查看|详情|搜索|扫一扫|打开通知|"
    r"不|回|简介|合作|帮助|导航|清空|在线|立即|投递|沟通|刷新|进入|报名|申请|"
    r"微信|公众号|企业|认证|会员|登录|注册|下载APP|APP)"
)


def _split_title_salary(line: str) -> tuple[str, str]:
    """从一行拆出 (title, salary_text)。无薪资返回 ("", "")；纯薪资行 title 为空但 salary 保留。"""
    m = _SALARY_RE.search(line)
    if not m:
        return "", ""
    start = m.start()
    title = line[:start].strip()
    salary_text = line[start:].strip()
    if len(title) < 2:
        return "", salary_text
    return title, salary_text


def _is_salary_line(line: str) -> bool:
    return bool(_SALARY_RE.search(line)) or bool(_SALARY_FLAT_RE.search(line))


def _is_company_line(line: str) -> bool:
    if _is_salary_line(line) or len(line) > 30:
        return False
    if _FOOTER_NOISE.search(line) or _UI_NOISE.search(line):
        return False
    return bool(_COMPANY_RE.search(line))


def _is_location_line(line: str) -> bool:
    if len(line) > 40 or _HR_RE.search(line):
        return False
    return bool(_LOCATION_RE.search(line)) or bool(_EXP_RE.search(line))


def _looks_like_title(line: str) -> bool:
    if not line or len(line) < 2 or len(line) > 20:
        return False
    if _is_salary_line(line) or _is_company_line(line) or _is_location_line(line):
        return False
    if _TITLE_BAD_RE.match(line):
        return False
    if _FOOTER_NOISE.search(line) or _UI_NOISE.search(line):
        return False
    # 排除以"XX招聘/XX银行"开头的整站链接（如 成都招聘、北京银行招聘）
    if re.search(r"[一-龥]{2,4}(招聘|银行招聘|招聘网)$", line):
        return False
    return True


def _find_nearest(
    lines: list[str],
    anchor: int,
    n: int,
    pred,
    direction: int,
    limit: int = 8,
) -> str:
    """从 anchor 向 direction(-1 上 / +1 下)找最近的满足 pred 的行，遇其他薪资行停止。"""
    if direction < 0:
        rng = range(anchor - 1, max(anchor - limit - 1, -1), -1)
    else:
        rng = range(anchor + 1, min(anchor + limit + 1, n))
    for j in rng:
        if pred(lines[j]):
            return lines[j]
        if j != anchor and _is_salary_line(lines[j]):
            break
    return ""


def parse_jobs_from_text(text: str) -> list[dict]:
    """从 OCR 文本提取岗位 dict 列表。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return []

    jobs: list[dict] = []
    seen: set[tuple] = set()
    n = len(lines)

    for i, line in enumerate(lines):
        title_inline, salary = _split_title_salary(line)
        if not salary:
            continue  # 本行不含薪资 → 不是卡片锚点

        # title：优先行内值（A 结构），否则双向找最近的标题行
        title = title_inline
        if not title:
            title = (
                _find_nearest(lines, i, n, _looks_like_title, -1)
                or _find_nearest(lines, i, n, _looks_like_title, +1)
            )

        company = _find_nearest(lines, i, n, _is_company_line, -1) or _find_nearest(
            lines, i, n, _is_company_line, +1
        )
        location = _find_nearest(lines, i, n, _is_location_line, +1) or _find_nearest(
            lines, i, n, _is_location_line, -1
        )

        _add_job(jobs, seen, title, salary, company, location)

    return jobs


def _add_job(jobs: list[dict], seen: set, title: str, salary_text: str, company: str, location: str) -> None:
    if not title:
        return
    key = (title, salary_text)
    if key in seen:
        return
    seen.add(key)
    jobs.append({
        "title": title,
        "salary_text": salary_text,
        "company": company,
        "location": location,
    })
