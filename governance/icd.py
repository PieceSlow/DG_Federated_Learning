"""ICD 编码 → 疾病/操作章节。

放在独立模块，是为了让 ``labels/engine`` 与 ``label`` 都能用同一套章节映射：
章节既是事件列（``clinical_events.icd_chapter``），也是图谱节点属性到标签的桥梁。
放在 ``label`` 里会造成 ``engine → label → labels`` 的循环导入。
"""

from __future__ import annotations

import re

ICD10_CHAPTERS = [
    ("A00-B99", "Certain infectious and parasitic diseases", "AB"),
    ("C00-D49", "Neoplasms", "CD"),
    ("E00-E89", "Endocrine, nutritional and metabolic diseases", "E"),
    ("F01-F99", "Mental, behavioural and neurodevelopmental disorders", "F"),
    ("G00-G99", "Diseases of the nervous system", "G"),
    ("H00-H59", "Diseases of the eye and adnexa", "H0"),
    ("H60-H95", "Diseases of the ear and mastoid process", "H6"),
    ("I00-I99", "Diseases of the circulatory system", "I"),
    ("J00-J99", "Diseases of the respiratory system", "J"),
    ("K00-K95", "Diseases of the digestive system", "K"),
    ("L00-L99", "Diseases of the skin and subcutaneous tissue", "L"),
    ("M00-M99", "Diseases of the musculoskeletal system and connective tissue", "M"),
    ("N00-N99", "Diseases of the genitourinary system", "N"),
    ("O00-O9A", "Pregnancy, childbirth and the puerperium", "O"),
    ("P00-P96", "Certain conditions originating in the perinatal period", "P"),
    ("Q00-Q99", "Congenital malformations and chromosomal abnormalities", "Q"),
    ("R00-R99", "Symptoms, signs and abnormal findings", "R"),
    ("S00-T88", "Injury, poisoning and external causes", "ST"),
    ("V00-Y99", "External causes of morbidity", "VY"),
    ("Z00-Z99", "Factors influencing health status", "Z"),
]

ICD9_CHAPTERS = [
    (1, 139, "Infectious and parasitic diseases"),
    (140, 239, "Neoplasms"),
    (240, 279, "Endocrine and metabolic diseases"),
    (280, 289, "Diseases of the blood"),
    (290, 319, "Mental disorders"),
    (320, 389, "Diseases of the nervous system"),
    (390, 459, "Diseases of the circulatory system"),
    (460, 519, "Diseases of the respiratory system"),
    (520, 579, "Diseases of the digestive system"),
    (580, 629, "Diseases of the genitourinary system"),
    (630, 679, "Pregnancy and childbirth"),
    (680, 709, "Diseases of the skin"),
    (710, 739, "Diseases of the musculoskeletal system"),
    (740, 759, "Congenital anomalies"),
    (760, 779, "Perinatal conditions"),
    (780, 799, "Symptoms and signs"),
    (800, 999, "Injury and poisoning"),
]


def icd_chapter(version: int, code: str) -> str:
    """把 ICD 编码映射到章节标签；无法归类时给明确兜底值而不是空串。"""
    code = str(code).strip().upper()
    if not code:
        return "Unknown"
    if int(version) == 10:
        letter = code[0]
        three = code[:3]
        if letter in "AB":
            return ICD10_CHAPTERS[0][1]
        if letter in "CD":
            return ICD10_CHAPTERS[1][1]
        if three.startswith("H0") or (
            letter == "H" and len(code) > 1 and code[1].isdigit() and int(code[1]) <= 5
        ):
            return ICD10_CHAPTERS[5][1]
        if letter == "H":
            return ICD10_CHAPTERS[6][1]
        for _, label, letters in ICD10_CHAPTERS[2:]:
            if letter == letters[0]:
                return label
        return "Other / unclassified"
    digits = re.match(r"(\d{1,3})", code)
    if not digits:
        return "Unknown"
    number = int(digits.group(1))
    for low, high, label in ICD9_CHAPTERS:
        if low <= number <= high:
            return label
    return "Other / unclassified"
