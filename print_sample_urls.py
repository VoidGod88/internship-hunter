"""
print_sample_urls.py — 用当前 config 值生成各平台示例 URL，方便用户检查。
使用方法：python print_sample_urls.py
"""
import sys
from pathlib import Path

# 把项目根目录加入 sys.path，方便 import config
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

import config as _config

kw = "python"
kw_slug = kw.lower().replace(" ", "-")

print("=" * 80)
print("  各平台示例 URL（使用当前 config.yaml 值）")
print("  示例关键词：", kw)
print("=" * 80)

# ── LinkedIn ────────────────────────────────────────────────────────────────────
print("\n【LinkedIn】")
params = []
params.append(f"keywords={kw.replace(' ', '+')}")
params.append("location=Hong%20Kong%20SAR")
params.append(f"geoId={_config.config.li_geo_id}")
if _config.config.li_exp_level:
    params.append(f"f_E={','.join(_config.config.li_exp_level)}")
if _config.config.li_job_types:
    params.append(f"f_JT={','.join(_config.config.li_job_types)}")
if _config.config.li_work_types:
    params.append(f"f_WT={','.join(_config.config.li_work_types)}")
if _config.config.li_sort_by:
    params.append(f"sort={_config.config.li_sort_by}")
if _config.config.li_posted_within:
    params.append(f"f_TPR={_config.config.li_posted_within}")
url_li = "https://www.linkedin.com/jobs/search/?" + "&".join(params)
print(f"  experience_level = {_config.config.li_exp_level}  (空=不过滤)")
print(f"  job_types        = {_config.config.li_job_types}")
print(f"  work_types       = {_config.config.li_work_types}")
print(f"  geo_id           = {_config.config.li_geo_id}")
print(f"  URL:\n  {url_li}")

# ── JobsDB ──────────────────────────────────────────────────────────────────────
print("\n【JobsDB】")
category = _config.config.jd_category or "information-communication-technology"
path = f"https://hk.jobsdb.com/{kw_slug}-jobs-in-{category}/in-hong-kong"
qparams = []
if _config.config.jd_work_type:
    wt = ",".join(str(v) for v in _config.config.jd_work_type)
    qparams.append(f"worktype={wt}")
if _config.config.jd_work_arrangement:
    wa = ",".join(str(v) for v in _config.config.jd_work_arrangement)
    qparams.append(f"workarrangement={wa}")
if _config.config.jd_daterange:
    qparams.append(f"daterange={_config.config.jd_daterange}")
url_jd = path + "?" + "&".join(qparams) if qparams else path
print(f"  category         = {category}")
print(f"  work_type        = {_config.config.jd_work_type}")
print(f"  work_arrangement = {_config.config.jd_work_arrangement}")
print(f"  daterange        = {_config.config.jd_daterange}")
print(f"  URL:\n  {url_jd}")

# ── Indeed ──────────────────────────────────────────────────────────────────────
print("\n【Indeed】")
params = [f"q={kw.replace(' ', '+')}"]
if _config.config.id_date_range:
    params.append(f"fromage={_config.config.id_date_range}")
if _config.config.id_sort_by:
    params.append(f"sort={_config.config.id_sort_by}")
if _config.config.id_radius:
    params.append(f"radius={_config.config.id_radius}")

# Build sc= param from encrypted filters (education + job types)
edu_valid = {"HFDVW","EXSNN","6QC5F","MR89S"}
jt_sc_valid = {"VDTG7","75GKK","T9BXE","CF3CP","5QWDV",
                "T65DZ","7EQCZ","2X29N","ZG59D"}
edu_codes = [c for c in _config.config.id_education if c in edu_valid]
jt_sc_codes = [c for c in _config.config.id_job_types_sc if c in jt_sc_valid]
if edu_codes or jt_sc_codes:
    if edu_codes and not jt_sc_codes:
        if len(edu_codes) == 1:
            sc_val = f"0kf%3Aattr%28{edu_codes[0]}%29%3B"
        else:
            sc_val = f"0kf%3Aattr%28{'%7C'.join(edu_codes)}%252COR%29%3B"
    elif jt_sc_codes and not edu_codes:
        if len(jt_sc_codes) == 1:
            sc_val = f"0kf%3Aattr%28{jt_sc_codes[0]}%29%3B"
        else:
            sc_val = f"0kf%3Aattr%28{'%7C'.join(jt_sc_codes)}%252COR%29%3B"
    else:
        edu_part = '%7C'.join(edu_codes) if len(edu_codes) > 1 else edu_codes[0]
        jt_part = '%7C'.join(jt_sc_codes) if len(jt_sc_codes) > 1 else jt_sc_codes[0]
        sc_val = f"0kf%3Aattr%28{jt_part}%29%29attr%28{edu_part}%29%3B"
    params.append(f"sc={sc_val}")
    print(f"  education codes  = {edu_codes}")
    print(f"  job_type_sc codes= {jt_sc_codes}")
params.append("l=Hong+Kong")
url_id = "https://hk.indeed.com/jobs?" + "&".join(params)
print(f"  date_range       = {_config.config.id_date_range}")
print(f"  job_types_sc     = {getattr(_config.config, 'id_job_types_sc', [])!r}")
print(f"  education        = {_config.config.id_education!r}")
print(f"  sort_by          = {_config.config.id_sort_by}")
print(f"  radius           = {_config.config.id_radius}")
print(f"  URL:\n  {url_id}")

# ── eFinancialCareers ────────────────────────────────────────────────────────
print("\n【eFinancialCareers】")
slug = kw.lower().replace(" ", "-")
params = [
    f"q={kw}",
    "countryCode=HK",
    "radius=40",
    "radiusUnit=km",
    f"pageSize={_config.config.efc_page_size or '15'}",
    "filters.locationPath=Asia%2FHong+Kong",
    "currencyCode=HKD",
    "language=en",
    "includeUnspecifiedSalary=true",
    "enableVectorSearch=true",
]
if _config.config.efc_exp_level:
    el = "%7C".join(_config.config.efc_exp_level)
    params.append(f"filters.experienceLevel={el}")
if _config.config.efc_posted_within:
    params.append(f"filters.postedWithin={_config.config.efc_posted_within}")
url_efc = f"https://www.efinancialcareers.hk/jobs/{slug}/in-hong-kong?" + "&".join(params)
print(f"  experience_level  = {_config.config.efc_exp_level}")
print(f"  posted_within    = {_config.config.efc_posted_within!r}  (空=不过滤)")
print(f"  page_size        = {_config.config.efc_page_size or '15'}")
print(f"  URL:\n  {url_efc}")

print("\n" + "=" * 80)
print("  提示：打开上方 URL 确认能正常加载搜索结果页，即表示 filter 正确")
print("=" * 80)
