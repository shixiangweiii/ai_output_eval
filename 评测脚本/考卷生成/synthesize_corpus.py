# -*- coding: utf-8 -*-
"""按"模板 + 取值表"合成同域兄弟语料，并产出事实台账的 facts 段。

为什么这么做：v3 语料是 10 篇互不重叠领域的文档，文档级召回 0.83 基本是白送，
真实 RAG 最常见的失败（内容对了但版本/主体错了）在那种语料里物理上无法发生。
这里每个族的 5 篇兄弟文档结构严格平行、**只在判别值上分叉**，"选对文档"才第一次
成为真问题。

每条事实在每篇文档里出现两次（home 一次、xref 一次），措辞不同、位置拉开，
这是 accepted_spans 能有 ≥2 个合法证据位置的物质基础。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BRIDGE_PATH = Path(__file__).with_name("bridge_chains.py")
BRIDGE_SPEC = importlib.util.spec_from_file_location("bridge_chains", BRIDGE_PATH)
if BRIDGE_SPEC is None or BRIDGE_SPEC.loader is None:  # pragma: no cover - 环境损坏
    raise ImportError(f"无法加载桥接链路规格: {BRIDGE_PATH}")
bridge = importlib.util.module_from_spec(BRIDGE_SPEC)
BRIDGE_SPEC.loader.exec_module(bridge)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = PROJECT_ROOT / "生成的原始文档语料" / "2026-08-04-02-真多跳"

IMAGE_BASE = "https://upload.wikimedia.org/wikipedia/commons"

# --------------------------------------------------------------------------
# A 族：口服抗凝药物用药安全警戒（混淆轴 = 版本）
# --------------------------------------------------------------------------

FAMILY_A = {
    "id": "A",
    "cluster_id": "C-A-anticoag",
    "axis": "version",
    "title": "口服抗凝药物用药安全警戒简报",
    "code": "AC-WARN",
    "documents": [
        {"name": "A1-anticoag-2023.md", "label": "2023 版", "status": "已归档",
         "note": "本版首次把围手术期停药与肾功能分层合并成一张判定表，原分散在两份附件里。"},
        {"name": "A2-anticoag-2024.md", "label": "2024 版", "status": "已归档",
         "note": "本版根据上一年度出血事件复盘，整体收紧了监测口径，并新增了培训学时要求。"},
        {"name": "A3-anticoag-2025.md", "label": "2025 版", "status": "现行",
         "note": "本版为当前执行版本，所有科室自发布之日起按本版执行，旧版仅作追溯参考。"},
        {"name": "A4-anticoag-2025r.md", "label": "2025 修订版", "status": "征求意见",
         "note": "本版是在现行版基础上的收紧草案，尚在征求意见期，未经药事会通过前不得据此下医嘱。"},
        {"name": "A5-anticoag-retired.md", "label": "2021 版", "status": "已废止",
         "note": "本版已于两次换版后废止，仅用于历史病历追溯；任何按本版口径开具的新医嘱都属于差错。"},
    ],
    "facts": [
        {
            "fact_id": "F-A-INR", "dimension": "华法林 INR 目标区间上限",
            "values": {"A1": "3.0", "A2": "2.9", "A3": "2.8", "A4": "2.7", "A5": "3.2"},
            "home": "本版把华法林 INR 目标区间上限设为 {v}，超过该值即判定为抗凝过度",
            "xref": "注意此处的判断基准是 {v} 这一封顶值，跨版本引用时务必核对",
        },
        {
            "fact_id": "F-A-CRCL", "dimension": "达比加群减量的肌酐清除率阈值",
            "values": {"A1": "45 mL/min", "A2": "42 mL/min", "A3": "40 mL/min",
                       "A4": "38 mL/min", "A5": "50 mL/min"},
            "home": "达比加群在肌酐清除率低于 {v} 时必须减量，不得维持原剂量",
            "xref": "减量触发线仍以 {v} 为准，检验科报告需附实测值",
        },
        {
            "fact_id": "F-A-HOLD", "dimension": "择期手术前停药时长",
            "values": {"A1": "72 小时", "A2": "60 小时", "A3": "48 小时",
                       "A4": "36 小时", "A5": "96 小时"},
            "home": "择期手术前应至少停药 {v}，急诊手术另按拮抗流程处理",
            "xref": "停药窗口按 {v} 倒推排期，麻醉访视时需二次确认",
        },
        {
            "fact_id": "F-A-AGE", "dimension": "高龄自动减量起始年龄",
            "values": {"A1": "78 岁", "A2": "76 岁", "A3": "75 岁",
                       "A4": "74 岁", "A5": "80 岁"},
            "home": "患者达到 {v} 及以上时进入自动减量通道，由药师复核后执行",
            "xref": "自动减量的年龄门槛是 {v}，系统会在开方环节直接拦截",
        },
        {
            "fact_id": "F-A-PLT", "dimension": "血小板计数停药下限",
            "values": {"A1": "60×10⁹/L", "A2": "55×10⁹/L", "A3": "50×10⁹/L",
                       "A4": "45×10⁹/L", "A5": "70×10⁹/L"},
            "home": "血小板计数跌破 {v} 应立即停药并请血液科会诊",
            "xref": "停药红线设在 {v}，复查间隔不得超过两个工作日",
        },
        {
            "fact_id": "F-A-REPORT", "dimension": "严重出血事件上报时限",
            "values": {"A1": "24 小时", "A2": "12 小时", "A3": "8 小时",
                       "A4": "6 小时", "A5": "48 小时"},
            "home": "严重出血事件须在发现后 {v} 内完成院内上报，逾期计入科室质控扣分",
            "xref": "上报计时从发现时刻起算，窗口为 {v}，不因交接班顺延",
        },
        {
            "fact_id": "F-A-HB", "dimension": "血红蛋白预警下限",
            "values": {"A1": "90 g/L", "A2": "95 g/L", "A3": "100 g/L",
                       "A4": "105 g/L", "A5": "85 g/L"},
            "home": "血红蛋白低于 {v} 触发出血预警，需当日复查并记录",
            "xref": "预警阈值 {v} 由检验系统自动比对，不接受手工豁免",
        },
        {
            "fact_id": "F-A-DUAL", "dimension": "双联抗栓最长疗程",
            "values": {"A1": "12 个月", "A2": "9 个月", "A3": "6 个月",
                       "A4": "3 个月", "A5": "18 个月"},
            "home": "双联抗栓总疗程上限为 {v}，超期需重新评估获益风险比",
            "xref": "疗程封顶 {v}，延长申请必须经心内与药学双签",
        },
        {
            "fact_id": "F-A-REVIEW", "dimension": "用药重整复核周期",
            "values": {"A1": "90 天", "A2": "60 天", "A3": "45 天",
                       "A4": "30 天", "A5": "180 天"},
            "home": "长期服药患者的用药重整复核周期不得长于 {v}",
            "xref": "复核周期 {v} 是硬性上限，门诊与住院口径一致",
        },
        {
            "fact_id": "F-A-TRAIN", "dimension": "处方权培训学时",
            "values": {"A1": "16 学时", "A2": "20 学时", "A3": "24 学时",
                       "A4": "28 学时", "A5": "8 学时"},
            "home": "取得抗凝处方权需完成 {v} 的专项培训并通过考核",
            "xref": "培训学时要求为 {v}，未达标者处方权自动冻结",
        },
    ],
    "asset": {
        "fact_id": "F-A-IMG", "dimension": "处置流程图",
        "urls": {
            "A1": f"{IMAGE_BASE}/9/93/The_Risk_Management_Process.png",
            "A2": f"{IMAGE_BASE}/9/93/The_Risk_Management_Process.png",
            "A3": f"{IMAGE_BASE}/7/7e/Flowchart_Process.svg",
            "A4": f"{IMAGE_BASE}/7/7e/Flowchart_Process.svg",
            "A5": f"{IMAGE_BASE}/a/a5/BalanceSheet.jpg",
        },
        "caption": "出血事件处置流程",
    },
    "sections": [
        ("1. 适用范围与版本说明",
         "本简报由药学部安全警戒组编制，覆盖华法林、达比加群、利伐沙班三类口服抗凝药物在住院与门诊场景下的用药安全要求。"
         "文中所有阈值均为院内执行口径，与国家指南存在差异时以本院药事会决议为准。使用前请先核对页眉的版本号与状态："
         "不同版本之间多处阈值并不相同，直接套用其他版本的数值是本院近三年最常见的用药差错来源之一。"),
        ("2. 抗凝强度目标", None),
        ("3. 肾功能与剂量调整", None),
        ("4. 围手术期停药管理", None),
        ("5. 特殊人群用药", None),
        ("6. 血象监测要求", None),
        ("7. 出血事件识别", None),
        ("8. 事件上报与追溯", None),
        ("9. 抗栓联合治疗", None),
        ("10. 用药重整", None),
        ("11. 处方权与培训", None),
        ("12. 附图与变更记录", None),
    ],
    "fillers": {
        "2": ("抗凝强度的把握是本简报的核心。强度不足会让血栓风险回升，强度过高则直接转化为出血事件，"
              "两侧都不是安全区。临床上更容易被忽略的是后者：抗凝过度往往没有即时症状，等到出现黑便或"
              "皮下瘀斑时已经错过了最佳干预窗口。因此本院要求把强度判定写进每日查房记录，而不是只在"
              "复查检验时才回看一次。",
              "另外提醒一句，强度目标与后文的培训要求是配套的：没有完成专项培训的医师即使看懂了本节，"
              "系统也不会放行处方。"),
        "3": ("肾功能是所有直接口服抗凝药物剂量决策的第一变量。达比加群约八成经肾排泄，肾功能下降时血药"
              "浓度上升非常快，这也是本类药物出血事件里占比最高的一类诱因。请注意肌酐清除率与估算肾小球"
              "滤过率并不等价，本院统一采用前者，检验系统的默认报告口径已做过相应调整。",
              "剂量调整之后仍需回看抗凝强度是否落在目标区间内，两者是同一个判断的两面。"),
        "4": ("围手术期是抗凝管理最容易出事的环节，因为它同时涉及外科、麻醉和药学三方，任何一方按了"
              "不同版本的口径执行都会出现停药时长不一致。本院的做法是由手术排期系统统一倒推，医师端"
              "只做确认不做计算。桥接抗凝并非常规选项，仅在机械瓣膜等少数指征下启用。",
              "停药决策还需要同时看肾功能：肾功能越差，药物清除越慢，实际所需的停药时间往往比标称值更长。"),
        "5": ("高龄、低体重和多重用药常常同时出现在同一位患者身上，这类患者的出血风险并不是各因素的"
              "简单叠加，而是明显的乘数效应。本院对高龄单列了自动减量通道，就是为了避免把判断完全"
              "交给个体经验。需要强调的是，自动减量不等于自动安全，减量后仍要按常规频次复查。",
              "如果患者同时处在围手术期，停药安排优先于减量安排，两条规则冲突时以停药口径为准。"),
        "6": ("血象监测的目的不是等出血发生后再确认，而是在数值出现趋势性下滑时提前干预。本院要求"
              "把血小板与血红蛋白放在同一张趋势图上看，单次数值正常但连续三次下行同样需要处理。"
              "检验危急值会直接推送到主管医师工作站，不依赖医师主动查询。",
              "血象异常时还应回顾患者年龄是否已进入自动减量区间，很多病例的根因在剂量而非血液系统本身。"),
        "7": ("出血事件的识别要区分「轻微」「临床相关非严重」和「严重」三档。严重出血指致命性出血、"
              "关键部位出血或血红蛋白显著下降并需要输血的情形。轻微出血虽然不需要立即停药，但连续"
              "发生同样是剂量偏高的信号，应当纳入下一次复核。",
              "识别之后紧接着就是血小板复查，两个动作在时间上几乎重叠，不要拆成两次操作。"),
        "8": ("上报不是行政流程而是安全屏障：只有事件进入系统，才能触发跨科室的复盘和后续的口径修订。"
              "本院近年多次阈值调整都直接来自上报数据的回溯分析。上报内容应包含事件时间、当时的用药"
              "方案、最近一次相关检验值以及已采取的处置措施。",
              "上报时同步附上血红蛋白的变化曲线，能显著缩短复盘环节的往返沟通。"),
        "9": ("抗凝与抗血小板联用会把出血风险抬到单药的数倍，因此疗程管理比强度管理更需要刚性约束。"
              "临床上常见的问题不是不知道要停，而是没有人负责在到期时提醒停。本院已把疗程到期提醒"
              "接入医嘱系统，到期前十四天开始每日提示。",
              "疗程管理与事件上报口径相关联：联合治疗期间发生的出血事件在上报时需单独标注。"),
        "10": ("用药重整是长期治疗中唯一能系统性发现「药物越加越多」的环节。重整时要逐条核对适应证是否"
               "仍然成立、剂量是否仍与当前肾功能匹配、有无可以停用的重复品种。门诊患者的重整常常被"
               "跳过，这一点在本版中被列为质控重点。",
               "重整完成后如涉及联合治疗方案变更，需同步更新疗程起算日，不得沿用旧的到期日。"),
        "11": ("处方权管理的意义在于把口径的一致性前置到人，而不是事后靠检查纠偏。培训内容覆盖本简报"
               "全部章节，考核形式为病例判读，及格线为八十分。培训记录与处方权状态在系统中直接联动，"
               "无需人工申请或注销。",
               "培训考核的题目会随版本更新，参加过旧版培训的医师在换版后需补齐差异部分。"),
        "12": ("以下附图给出出血事件从识别到上报的完整处置路径，供打印张贴使用。图中的分支判断与正文"
               "一致，若发现图文不符请以正文为准并反馈至药学部。",
               "本版的用药重整周期与培训学时是与上一版差异最大的两项，换版培训时应重点说明。"),
    },
    "padding": [
        "从既往的不良事件分析看，多数问题并不出在医师不知道规则，而是出在规则在不同文件里存在多个版本，"
        "而床旁能拿到的往往是最先被打印出来的那一份。本院为此把所有现行口径统一收敛到电子病历系统的"
        "提示卡上，纸质材料仅作为培训辅助，不作为执行依据。请各科室在换版后主动清理旧版打印件。",
        "需要特别说明的是，本简报中的阈值都是院内执行口径，它们通常比公开指南更严，原因是本院承担了较多"
        "高龄和多病共存的患者，人群基线风险本身就更高。若患者来自外院转诊并携带外院的用药方案，应按本院"
        "口径重新评估，而不是直接延续原方案，这一点在交接班记录中必须明确写出。",
        "临床上真正难的从来不是单一指标的判断，而是多个指标同时越界时的优先级。总体原则是：出血相关的"
        "停药类指令优先于剂量调整类指令，剂量调整类指令又优先于监测频次类指令。当三类指令互相冲突且"
        "无法当场判定时，应当先执行最保守的那一条，同时呼叫临床药师到场。",
        "药学部每季度会把本简报涉及的全部阈值做一次横向核对，核对结果连同差错案例一并发布。历史数据显示，"
        "换版后的第一个月是差错高发期，主要集中在按旧版口径开具的长期医嘱没有及时重整。建议各科室在换版"
        "当月把长期医嘱清单过一遍，而不是等到常规复核周期到期。",
        "关于记录的完整性：任何一次偏离本简报的处置都应当在病历中写明理由，包括当时依据的检验值、会诊"
        "意见和患者本人的意愿。这不是为了追责，而是因为后续的口径修订完全依赖这些记录。缺少理由的偏离"
        "在质控评分中会被直接判定为不合规，即使临床结局良好。",
        "本院的信息系统已经把本简报的主要判断点做成了硬拦截或软提示两类。硬拦截无法绕过，软提示允许"
        "医师填写理由后继续。哪些点做成硬拦截是药事会讨论后确定的，原则是只有那些一旦越界就几乎必然"
        "造成伤害的项才设为硬拦截，其余保留临床裁量空间。",
        "跨科室会诊时经常出现的一个误解，是把本简报当成只适用于心内科的文件。实际上骨科、消化和急诊"
        "同样在使用抗凝药物，且这些科室的患者往往合并创伤或活动性出血，风险画像与心内科差异很大。"
        "本简报对所有开具抗凝处方的科室一体适用，不存在科室豁免。",
        "对于门诊长期随访的患者，最大的风险来自失访。患者自行停药或自行调整剂量的比例远高于住院场景，"
        "而这些变化在下一次就诊前无法被系统感知。本院的做法是把随访提醒与药品配送记录做关联，配送中断"
        "超过一个周期即触发随访电话。",
        "培训与考核之所以被写进本简报而不是单独成文，是因为口径的一致性最终要落到具体的人。历次复盘"
        "显示，同一科室内不同资历医师对同一条规则的理解差异，往往比不同科室之间的差异更大。定期的"
        "病例判读考核是目前唯一被证明有效的收敛手段。",
        "最后需要提醒的是版本引用问题。本简报的每一次修订都会同时保留旧版供追溯，但追溯用途不等于执行"
        "依据。在检索或查阅系统中同时看到多个版本时，请以状态字段为准：只有标注为现行的版本才可以作为"
        "开具医嘱的依据，标注为征求意见或已废止的版本一律不得据此下医嘱。",
        "本节涉及的判断在实际工作中往往需要结合患者的整体情况，单看一个数值容易产生误判。建议在查房时"
        "把相关指标与患者近期的临床表现放在一起评估，尤其要关注那些数值尚未越界但趋势明显不利的病例，"
        "这类病例在事后复盘中占比很高。",
        "各科室在执行过程中如发现本节要求与实际工作流程存在冲突，应当通过正式渠道反馈，由药学部汇总后"
        "提交药事会讨论。在新的决议出台之前，仍需按现行口径执行。私下形成的科室内部惯例即使运行多年，"
        "也不能作为偏离本简报的理由。",
    ],
}

# --------------------------------------------------------------------------
# B 族：消费信贷智能风控体系设计说明书（混淆轴 = 主体机构）
# --------------------------------------------------------------------------

FAMILY_B = {
    "id": "B",
    "cluster_id": "C-B-riskctl",
    "axis": "entity",
    "title": "消费信贷智能风控体系设计说明书",
    "code": "RISK-DS",
    "documents": [
        {"name": "B1-risk-hongyuan.md", "label": "宏元消费金融", "status": "生效",
         "note": "宏元的客群以线上小额分期为主，模型迭代频次高，风控口径整体偏激进。"},
        {"name": "B2-risk-jinzhou.md", "label": "瑾洲银行信用卡中心", "status": "生效",
         "note": "瑾洲受银行体系统一管控，各项阈值普遍比非银机构更保守，审批链路也更长。"},
        {"name": "B3-risk-nanxu.md", "label": "南序小额贷款", "status": "生效",
         "note": "南序以线下门店获客为主，人工复核占比高，模型只作为辅助决策手段。"},
        {"name": "B4-risk-qihe.md", "label": "岐禾数科", "status": "试运行",
         "note": "岐禾是集团内新设的科技子公司，本说明书处于试运行期，阈值仍在按周校准。"},
        {"name": "B5-risk-group.md", "label": "集团总纲", "status": "生效",
         "note": "本文件是集团层面的底线要求，各成员机构可在此基础上收紧，但不得放宽。"},
    ],
    "facts": [
        {
            "fact_id": "F-B-CUTOFF", "dimension": "评分卡自动通过分数线",
            "values": {"B1": "612 分", "B2": "648 分", "B3": "630 分",
                       "B4": "605 分", "B5": "620 分"},
            "home": "评分卡自动通过线设在 {v}，达到该线的申请直接进入放款队列",
            "xref": "自动通过线 {v} 由风险管理部统一调参，业务侧无权自行调整",
        },
        {
            "fact_id": "F-B-REJECT", "dimension": "自动拒绝分数线",
            "values": {"B1": "480 分", "B2": "520 分", "B3": "500 分",
                       "B4": "465 分", "B5": "495 分"},
            "home": "低于 {v} 的申请直接自动拒绝，不再进入人工环节",
            "xref": "自动拒绝线取 {v}，该线以下的申诉需走独立复议通道",
        },
        {
            "fact_id": "F-B-DTI", "dimension": "债务收入比上限",
            "values": {"B1": "65%", "B2": "50%", "B3": "58%",
                       "B4": "70%", "B5": "60%"},
            "home": "申请人的债务收入比不得超过 {v}，超限一票否决",
            "xref": "债务收入比红线为 {v}，多头借贷数据需并表计算",
        },
        {
            "fact_id": "F-B-KS", "dimension": "模型上线最低 KS 值",
            "values": {"B1": "0.32", "B2": "0.38", "B3": "0.35",
                       "B4": "0.30", "B5": "0.34"},
            "home": "模型在跨时间验证集上的 KS 值不得低于 {v}，否则不予上线",
            "xref": "上线门槛 KS 为 {v}，验证集必须与训练期完全不重叠",
        },
        {
            "fact_id": "F-B-PSI", "dimension": "特征漂移下线阈值",
            "values": {"B1": "0.25", "B2": "0.15", "B3": "0.20",
                       "B4": "0.28", "B5": "0.18"},
            "home": "单特征 PSI 超过 {v} 即触发强制下线并回滚上一版本",
            "xref": "漂移熔断线是 {v}，监控频率为每日一次全量比对",
        },
        {
            "fact_id": "F-B-MANUAL", "dimension": "人工复核抽检比例",
            "values": {"B1": "8%", "B2": "20%", "B3": "35%",
                       "B4": "5%", "B5": "12%"},
            "home": "自动通过的申请需按 {v} 的比例抽检人工复核",
            "xref": "抽检比例为 {v}，样本由系统随机抽取，不接受人工指定",
        },
        {
            "fact_id": "F-B-LIMIT", "dimension": "新客首次授信上限",
            "values": {"B1": "2 万元", "B2": "5 万元", "B3": "3 万元",
                       "B4": "1.5 万元", "B5": "4 万元"},
            "home": "新客首次授信额度上限为 {v}，提额需在正常还款六期之后",
            "xref": "首次授信封顶 {v}，任何白名单都不得突破该上限",
        },
        {
            "fact_id": "F-B-OVERDUE", "dimension": "进入催收的逾期天数",
            "values": {"B1": "3 天", "B2": "7 天", "B3": "5 天",
                       "B4": "1 天", "B5": "4 天"},
            "home": "逾期满 {v} 后案件自动移交催收系统",
            "xref": "移交催收的时点是逾期满 {v}，节假日不顺延",
        },
        {
            "fact_id": "F-B-RETRAIN", "dimension": "模型重训周期",
            "values": {"B1": "每 45 天", "B2": "每 180 天", "B3": "每 90 天",
                       "B4": "每 30 天", "B5": "每 120 天"},
            "home": "评分模型的例行重训周期为 {v}，逾期未重训视为模型失效",
            "xref": "重训节奏保持 {v}，特征口径变更时需额外触发一次",
        },
        {
            "fact_id": "F-B-EXPLAIN", "dimension": "拒绝原因返回条数",
            "values": {"B1": "3 条", "B2": "5 条", "B3": "4 条",
                       "B4": "2 条", "B5": "3 条"},
            "home": "被拒申请人有权获得不少于 {v} 的主要拒绝原因说明",
            "xref": "原因说明数量下限为 {v}，措辞需通过合规话术库校验",
        },
    ],
    "asset": {
        "fact_id": "F-B-IMG", "dimension": "风控架构图",
        "urls": {
            "B1": f"{IMAGE_BASE}/1/13/Network_architecture_2levels.png",
            "B2": f"{IMAGE_BASE}/1/13/Network_architecture_2levels.png",
            "B3": f"{IMAGE_BASE}/3/39/Synergix_Technologies_Organization_Chart.png",
            "B4": f"{IMAGE_BASE}/3/39/Synergix_Technologies_Organization_Chart.png",
            "B5": f"{IMAGE_BASE}/5/5e/UK_tv_viewing_share_line_chart_1999_-_2009.png",
        },
        "caption": "风控决策链路架构",
    },
    "sections": [
        ("1. 文件适用范围",
         "本说明书描述消费信贷业务从申请受理到贷后管理的完整风控设计，供风险、科技与合规三方共同使用。"
         "集团内各机构均基于同一套框架落地，但由于客群结构、资金成本和监管要求不同，具体阈值存在差异。"
         "跨机构引用任何数值前请先确认页眉标注的适用主体：把其他机构的口径直接套到本机构上，是历次内审"
         "中被点名最多的问题。"),
        ("2. 评分卡与准入", None),
        ("3. 拒绝策略", None),
        ("4. 负债与多头风险", None),
        ("5. 模型效果验收", None),
        ("6. 模型监控与熔断", None),
        ("7. 人工复核机制", None),
        ("8. 额度策略", None),
        ("9. 贷后与催收", None),
        ("10. 模型迭代节奏", None),
        ("11. 可解释性与消费者权益", None),
        ("12. 架构图与修订记录", None),
    ],
    "fillers": {
        "2": ("评分卡是整条决策链路的第一道闸门，它决定了绝大多数申请的走向。设计上我们刻意让自动通过"
              "与自动拒绝之间留出一段灰区，灰区内的申请交由规则引擎和人工共同处理。灰区过窄会让通过率"
              "剧烈波动，过宽则会把成本压在人工侧，这个平衡需要按季度回看。",
              "准入判断完成后还要接着看负债情况，评分达标但负债超限的申请同样不能放行。"),
        "3": ("拒绝策略需要同时兼顾风险和体验。硬拒绝的边界必须清晰可解释，否则在消费者投诉和监管问询"
              "环节都会很被动。我们不建议用模型分数以外的隐性规则做拒绝，所有拒绝动作都要能追溯到一条"
              "明确的策略编号。",
              "拒绝之后必须向申请人返回原因说明，具体条数要求见后文的消费者权益章节。"),
        "4": ("多头借贷是消费信贷最主要的风险来源，单一机构的数据往往看不到全貌。本机构接入了行业共享"
              "查询与征信两路数据，并对同一自然人的多个身份标识做归并。需要提醒的是，负债计算口径必须"
              "包含未出账单的分期余额，只算已出账单会系统性低估负债水平。",
              "负债超限的案件即使评分很高也不得放行，这条与准入章节的分数线是并列条件而非替代条件。"),
        "5": ("模型验收关注的是跨时间稳定性，而不是训练集上的漂亮数字。我们要求验证集必须来自训练期之后"
              "的完整业务周期，且不做任何形式的重采样。除区分度指标外，还需提交分群稳定性、单调性检验"
              "和拒绝推断的敏感性分析。",
              "通过验收只是上线的必要条件，上线后的监控口径见下一节，两者的阈值是分开设定的。"),
        "6": ("上线后的监控比上线前的验收更重要，因为真正的风险来自分布漂移而非建模误差。我们对每个入模"
              "特征做逐日的分布比对，并对模型输出分数做整体稳定性监控。熔断动作是自动的，不需要等待"
              "人工确认，回滚后再由建模团队定位原因。",
              "熔断触发后应立即提高人工复核比例，直至新版本通过验收为止。监控看板上的每一次熔断都会"
              "保留现场快照，包括触发时刻的特征分布、样本量和上下游数据源状态，供事后定位使用。"),
        "7": ("人工复核的价值不在于逐笔纠错，而在于持续产出模型看不到的信号。复核员发现的异常模式会"
              "沉淀成新的规则或特征，形成闭环。抽检样本必须随机，一旦允许人工挑选样本，复核结论就"
              "失去了统计意义，这一点在历次审计中反复被强调。",
              "复核中发现的额度异常应单独标记，额度策略的调整依据主要来自这部分样本。"),
        "8": ("额度策略直接决定风险敞口的绝对规模。新客阶段信息最少，因此额度必须保守；随着还款行为"
              "数据积累，提额的置信度才会提高。我们不接受以营销活动为由的临时突破，所有提额都必须"
              "走同一套模型和审批流。",
              "额度决策还要参考贷后表现，尤其是历史逾期记录，具体口径见贷后章节。"),
        "9": ("贷后管理的第一原则是尽早介入。逾期天数越长，回收率下降越快，这条曲线在各机构的数据里"
              "都高度一致。催收动作必须严格遵守监管对时段、频次和话术的要求，任何外包环节都需纳入"
              "同等标准的质检。",
              "催收数据会回流到模型重训中，作为最重要的标签来源之一。"),
        "10": ("迭代节奏是风控团队最容易低估的一项设计。重训过慢会让模型逐步失效，过快则会引入过拟合"
               "和运维负担。我们的做法是设定固定的例行周期，同时允许在特征口径变更或监控告警时插入"
               "计划外重训。",
               "每次重训后都要重新走一遍验收流程，不能因为是例行迭代就跳过跨时间验证。"),
        "11": ("可解释性既是监管要求也是业务需要。申请人有权知道自己被拒的大致原因，而这些原因必须来自"
               "模型真实的贡献度排序，不能事后编造。我们用一套固定的映射把特征贡献翻译成客户能理解的"
               "表述，并由合规团队统一维护话术库。",
               "解释口径与拒绝策略必须一致，出现矛盾时以策略编号对应的原因为准。"),
        "12": ("下图给出从申请受理到贷后回流的完整决策链路，各机构的差异主要体现在阈值参数上，链路结构"
               "本身是一致的。若图与正文冲突，以正文为准。",
               "本次修订对模型迭代周期与人工复核比例做了同步调整，落地时需要一并变更。"),
    },
    "padding": [
        "集团内各机构使用同一套风控框架，但阈值必须各自标定，原因是客群结构差异会直接改变同一分数所对应的"
        "真实违约概率。把一家机构调好的参数平移到另一家，短期内看不出问题，通常要到第三或第四个账龄窗口"
        "才会暴露，那时候资产已经形成，纠偏成本极高。历次内审对此有过多次通报。",
        "风控参数的变更必须走版本管理，任何一次调整都要能回答三个问题：改了什么、依据是什么、预期影响多大。"
        "缺少其中任何一项的变更申请都会被风险管理部驳回。变更上线后的前两周属于观察期，观察期内需要每日"
        "输出通过率、审批时长和早期风险指标的对比，异常时立即回滚。",
        "科技侧与风险侧的职责边界需要说清楚：科技负责链路的可用性、时效和数据质量，风险负责策略本身的"
        "有效性。当线上指标异常时，第一步是判断属于哪一侧的问题，而不是同时改动两侧。同时改动会让归因"
        "彻底失效，这是过去几次线上事故复盘中反复出现的教训。",
        "数据源的稳定性往往被低估。外部数据供应商的接口变更、字段口径调整甚至采样策略的微调，都会以特征"
        "漂移的形式传导到模型上。我们要求对每个外部数据源建立独立的可用性与分布监控，并在合同中明确"
        "变更通知义务。没有监控的数据源不允许进入模型。",
        "关于成本：风控从来不是把风险压到最低，而是在既定的风险偏好下把通过率做到最高。过度保守的策略"
        "在报表上表现为漂亮的逾期率，实际却是把大量优质客户拒之门外，这部分损失不会出现在任何风险指标里，"
        "因此更需要主动测量。我们用拒绝客群的外部表现回溯来估算这部分机会成本。",
        "合规要求在近两年持续收紧，尤其是在自动化决策的告知义务和数据使用授权方面。所有涉及个人信息的"
        "特征都必须能追溯到明确的授权来源，无法追溯的特征一律下线，不接受任何形式的历史豁免。合规团队"
        "对特征清单的审查是季度例行动作，不需要业务侧发起。",
        "策略与模型的分工是：模型给出连续的风险度量，策略把度量转成离散的动作。两者的边界要清晰，避免"
        "在模型里嵌入业务规则，也避免在策略里做隐性的风险排序。混在一起最直接的后果是出问题时无法定位，"
        "以及模型重训后策略效果发生不可预期的漂移。",
        "关于灰区的处理：灰区是人工与自动的交界，也是最容易积压的环节。我们要求灰区案件的处理时效有明确"
        "承诺，超时未处理的案件按拒绝处理并允许申请人复议。把灰区无限期挂起既影响体验，也会让通过率的"
        "统计口径失真，进而影响后续的参数标定。",
        "各机构在落地时经常遇到的一个问题，是本机构的历史数据不足以支撑独立建模。此时可以采用集团层面的"
        "基础模型加本机构的校准层，但校准层的参数必须用本机构数据估计，不得直接沿用兄弟机构的取值。"
        "校准效果需要单独出具报告，并纳入验收范围。",
        "贷后数据的回流质量直接决定下一代模型的上限。标签定义的口径变化、催收策略调整带来的表现扰动、"
        "以及展期和重组产生的标签污染，都需要在建模前显式处理。我们要求建模文档中必须包含标签定义的"
        "完整说明，包括观察期、表现期和排除规则。",
        "本节所述的阈值都是本机构的执行口径，与集团总纲以及其他成员机构的取值并不相同。在跨机构的联合"
        "分析、监管报送或内部汇报中引用具体数值时，必须标明适用主体，否则很容易被误读成集团统一标准。"
        "过去的几次口径争议基本都源于此。",
        "最后关于文档本身：本说明书随策略变更同步更新，更新记录见末节。如果在检索系统中同时看到多份"
        "同名文件，请以页眉的适用主体和状态字段区分，不要仅凭标题判断。试运行状态的文件不作为线上执行"
        "依据，仅供内部评审使用。",
    ],
}

FAMILIES = [FAMILY_A, FAMILY_B]


def _doc_key(name: str) -> str:
    """A1-anticoag-2023.md → A1"""
    return name.split("-", 1)[0]


def render_document(family: Dict[str, Any], document: Dict[str, Any]) -> str:
    """把模板与该文档的取值渲染成一篇 Markdown。"""
    key = _doc_key(document["name"])
    facts = family["facts"]
    lines = [
        f"# {family['title']}（{document['label']}）",
        "",
        f"文件编号：{family['code']}-{key} ｜ 适用主体：{document['label']} ｜ 状态：{document['status']}",
        "",
        document["note"],
        "",
    ]
    for index, (title, fixed) in enumerate(family["sections"]):
        lines.append(f"## {title}")
        lines.append("")
        if fixed is not None:
            lines.append(fixed)
            lines.append("")
            continue
        number = title.split(".", 1)[0]
        head, tail = family["fillers"][number]
        padding = family["padding"]
        # 每节承载一条事实的 home 陈述与另一条事实的 xref 陈述；中间垫入足够篇幅，
        # 保证不同事实的 span 拉开距离，否则一个检索块会同时命中多道题
        home_fact = facts[index - 1] if index - 1 < len(facts) else None
        xref_fact = facts[index - 2] if 0 <= index - 2 < len(facts) else None
        if home_fact is not None:
            lines.append(home_fact["home"].format(v=home_fact["values"][key]) + "。")
            lines.append("")
        size = len(padding)
        lines.extend([
            head, "",
            padding[(index * 2 - 2) % size], "",
            padding[(index * 3 + 5) % size], "",
        ])
        if xref_fact is not None:
            lines.append(xref_fact["xref"].format(v=xref_fact["values"][key]) + "。")
            lines.append("")
        lines.extend([
            tail, "",
            padding[(index * 2 - 1) % size], "",
            padding[(index * 3 + 8) % size], "",
        ])
        if index == len(family["sections"]) - 1:
            asset = family["asset"]
            lines.append(f"![{asset['caption']}]({asset['urls'][key]})")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_fact_entries(family: Dict[str, Any]) -> List[Dict[str, Any]]:
    """产出台账的 facts 段：locator 由同一套模板确定性推导，无需人工誊抄。"""
    names = {_doc_key(d["name"]): d["name"] for d in family["documents"]}
    section_titles = [title for title, _ in family["sections"]]
    entries = []
    for index, fact in enumerate(family["facts"]):
        home_section = section_titles[index + 1]
        xref_section = section_titles[index + 2] if index + 2 < len(section_titles) else section_titles[-1]
        locations = {}
        for key, name in names.items():
            value = fact["values"][key]
            locations[name] = [
                {"section": home_section, "locator": fact["home"].format(v=value)},
                {"section": xref_section, "locator": fact["xref"].format(v=value)},
            ]
        entries.append({
            "fact_id": fact["fact_id"],
            "family": family["id"],
            "cluster_id": family["cluster_id"],
            "kind": "text",
            "claim_type": "anchor" if index % 2 == 0 else "passage",
            "dimension": fact["dimension"],
            "values": {names[key]: value for key, value in fact["values"].items()},
            "locations": locations,
        })
    asset = family["asset"]
    entries.append({
        "fact_id": asset["fact_id"],
        "family": family["id"],
        "cluster_id": family["cluster_id"],
        "kind": "asset",
        "claim_type": "passage",
        "dimension": asset["dimension"],
        "values": {names[key]: url for key, url in asset["urls"].items()},
        "locations": {
            names[key]: [{"section": section_titles[-1], "locator": url}]
            for key, url in asset["urls"].items()
        },
    })
    return entries


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="合成同域兄弟语料并产出台账 facts 段")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--facts-out", default=None, help="把 facts/families 段写到该 JSON")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    facts: List[Dict[str, Any]] = []
    families: List[Dict[str, Any]] = []
    for family in FAMILIES:
        for document in family["documents"]:
            text = render_document(family, document)
            (out_dir / document["name"]).write_text(text, encoding="utf-8")
            print(f"  {document['name']}: {len(text)} 字")
        facts.extend(build_fact_entries(family))
        families.append({
            "id": family["id"],
            "cluster_id": family["cluster_id"],
            "axis": family["axis"],
            "title": family["title"],
            "documents": [d["name"] for d in family["documents"]],
        })

    # 实体桥接链路：端点文档只含「端点人名 + 一个中间实体」，桥接文档只含中间实体，
    # 因此题面（只给两个端点人名）无法直接命中桥接文档，这一跳绕不过去。
    chains: List[Dict[str, Any]] = []
    chain_docs = 0
    for chain in bridge.CHAINS:
        for spec in chain["documents"]:
            (out_dir / spec["doc"]).write_text(
                bridge.render_chain_document(spec), encoding="utf-8"
            )
            facts.extend(bridge.build_chain_fact_entries(spec, chain))
            chain_docs += 1
        chains.append({
            "id": chain["chain_id"],
            "family": chain["family"],
            "cluster_id": chain["cluster_id"],
            "relation_type": chain["relation_type"],
            "path": chain["path"],
            "endpoint_entities": chain["endpoint_entities"],
            "bridge_entities": chain["bridge_entities"],
            "endpoint_documents": [
                s["doc"] for s in chain["documents"] if s["role"] == "endpoint"
            ],
            "bridge_documents": [
                s["doc"] for s in chain["documents"] if s["role"] == "bridge"
            ],
            "documents": [s["doc"] for s in chain["documents"]],
        })
        print(f"  链路 {chain['chain_id']}: {chain['path']}")

    for spec in bridge.DECOY_DOCUMENTS:
        (out_dir / spec["doc"]).write_text(
            bridge.render_chain_document(spec), encoding="utf-8"
        )
        facts.extend(bridge.build_chain_fact_entries(
            spec, {"family": "X", "cluster_id": None, "chain_id": None}
        ))
        chain_docs += 1
        print(f"  假桥接硬负例 {spec['doc']}")

    if args.facts_out:
        path = Path(args.facts_out).expanduser()
        path.write_text(
            json.dumps(
                {"families": families, "chains": chains, "facts": facts},
                ensure_ascii=False, indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        print(f"[facts 段] {len(facts)} 条事实 / {len(chains)} 条链 → {path}")
    total = sum(len(f["documents"]) for f in FAMILIES) + chain_docs
    print(f"[语料合成完成] {total} 篇 → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
