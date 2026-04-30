# 领域分类体系（Sector Taxonomy）

> **维护说明**：本文档为 `scraper/exa_search.py` 中 `FIRM_DOMAINS` 与 `SEARCH_CONFIGS` 的设计参考，
> 新增领域时同步更新此文件与代码。最后更新：2026-04-30。

---

## 目录

1. [投资阶段定义](#投资阶段定义)
2. [AI（应用端 + 硬件/机器人）](#1-ai应用端--硬件机器人)
3. [半导体与半导体设备](#2-半导体与半导体设备)
4. [医疗健康（医药 + 大健康）](#3-医疗健康医药--大健康)
5. [教育科技](#4-教育科技)
6. [金融科技与量化](#5-金融科技与量化)
7. [环保化工材料](#6-环保化工材料)
8. [能源与新能源](#7-能源与新能源)

---

## 投资阶段定义

用于筛选、标注和过滤投融资事件；正则均不区分大小写（`re.IGNORECASE`）。

| 阶段标签 | 中文名 | 英文关键词（含同义词） | 典型融资规模 | Python 正则匹配模式 |
|----------|--------|----------------------|-------------|---------------------|
| `angel` | 天使轮 | Angel, Pre-Seed, Pre Seed, Friends & Family | $50K – $2M | `angel\|pre[-\s]?seed\|friends.{0,5}family` |
| `pre_a` | Pre-A 轮 / 种子轮 | Seed, Seed+, Seed Round, Seed Plus, Bridge Seed | $1M – $8M | `\bseed(\+\|plus\|round)?\b\|bridge.{0,8}seed` |
| `series_abc` | A/B/C 轮 | Series A, Series B, Series C, Early Stage VC | $5M – $150M | `series\s?[abc]\b\|early.{0,6}stage.{0,6}(vc\|venture)` |
| `pre_ipo` | Pre-IPO / 后期轮 | Series D, Series E, Series F+, Late Stage, Growth Equity, Pre-IPO, Growth Round | $100M+ | `series\s?[d-z]\b\|late.{0,6}stage\|growth.{0,10}(equity\|round)\|pre.{0,3}ipo` |

> **使用示例**（Python）：
> ```python
> import re
> STAGE_PATTERNS = {
>     "angel":      re.compile(r"angel|pre[-\s]?seed|friends.{0,5}family", re.I),
>     "pre_a":      re.compile(r"\bseed(\+|plus|round)?\b|bridge.{0,8}seed", re.I),
>     "series_abc": re.compile(r"series\s?[abc]\b|early.{0,6}stage.{0,6}(vc|venture)", re.I),
>     "pre_ipo":    re.compile(r"series\s?[d-z]\b|late.{0,6}stage|growth.{0,10}(equity|round)|pre.{0,3}ipo", re.I),
> }
> ```

---

## 1. AI（应用端 + 硬件/机器人）

| 属性 | 值 |
|------|----|
| **中文名** | 人工智能（应用端 + 硬件/机器人） |
| **英文名** | Artificial Intelligence — Applications, Hardware & Robotics |
| **领域代码** | `ai` |

### 子领域细分

| 子领域（中文） | 子领域（英文） | 代表方向 |
|--------------|--------------|---------|
| 企业级 AI / AI SaaS | Enterprise AI / AI SaaS | Agents, Copilot, LLM API, 垂直行业 AI |
| 消费 AI | Consumer AI | 个人助手、AIGC 创作工具、推荐系统 |
| AI 基础设施 | AI Infrastructure | MLOps、Vector DB、AI Cloud、数据标注 |
| 具身智能 / 机器人 | Embodied AI / Robotics | 人形机器人、工业机器人、家庭机器人 |
| 自动驾驶 | Autonomous Vehicles | L2+~L4 乘用车/商用车/无人配送 |
| 计算机视觉 | Computer Vision | 工业质检、安防、医疗影像 |
| 多模态与大模型 | Multimodal & Foundation Models | LLM、VLM、语音模型 |
| AI 芯片（设计侧） | AI Chip Design | GPU、NPU、存内计算（设计公司） |

### Exa 搜索查询组

```text
1. "artificial intelligence venture capital firm investment portfolio enterprise AI"
2. "large language model LLM generative AI startup venture fund"
3. "AI agent enterprise SaaS series A venture capital"
4. "robotics embodied intelligence humanoid robot venture capital fund"
5. "autonomous vehicle self-driving AI startup investor portfolio"
6. "AI infrastructure MLOps vector database startup venture capital"
7. "foundation model multimodal AI startup VC investment thesis"
8. "computer vision industrial AI startup venture fund portfolio companies"
```

### 推荐 FIRM_DOMAINS

```python
# AI 专项 VC 官网 & 数据库
"a16z.com",           # Andreessen Horowitz (重仓 AI)
"sequoiacap.com",     # Sequoia Capital
"khoslaventures.com", # Khosla Ventures
"lightspeedvp.com",   # Lightspeed Venture Partners
"greylock.com",       # Greylock Partners
"benchmark.com",      # Benchmark
"accel.com",          # Accel
"nea.com",            # NEA
"indexventures.com",  # Index Ventures
"felicis.com",        # Felicis Ventures
"gv.com",             # Google Ventures
"spark.capital",      # Spark Capital
"radical.vc",         # Radical Ventures (AI-only fund)
"coatue.com",         # Coatue Management
"crunchbase.com",     # 数据聚合
"pitchbook.com",      # 数据聚合
```

---

## 2. 半导体与半导体设备

| 属性 | 值 |
|------|----|
| **中文名** | 半导体与半导体设备 |
| **英文名** | Semiconductors & Semiconductor Equipment |
| **领域代码** | `semiconductor` |

### 子领域细分

| 子领域（中文） | 子领域（英文） | 代表方向 |
|--------------|--------------|---------|
| 逻辑芯片 | Logic Chips | CPU、GPU、NPU、FPGA、SoC |
| 存储芯片 | Memory | DRAM、NAND Flash、新型存储（MRAM/ReRAM） |
| 模拟与混合信号 | Analog & Mixed-Signal | ADC/DAC、电源管理 IC、运算放大器 |
| 射频芯片 | RF & mmWave Chips | 5G/6G 射频前端、毫米波雷达 |
| 功率半导体 | Power Semiconductors | IGBT、SiC MOSFET、GaN 功率器件 |
| 半导体设备 | Semiconductor Equipment | 光刻机、刻蚀机、CVD/ALD 设备、量测设备 |
| 半导体材料 | Semiconductor Materials | 硅片、光刻胶、CMP 浆料、靶材 |
| EDA / IP | EDA Tools & IP | 设计工具、芯片 IP 核授权 |
| MEMS / 传感器 | MEMS & Sensors | 压力、惯性、生物传感器 |
| 先进封装 | Advanced Packaging | Chiplet、CoWoS、Fan-Out WLP |

### Exa 搜索查询组

```text
1. "semiconductor venture capital fund chip startup investment portfolio"
2. "fabless semiconductor AI chip startup series A venture capital"
3. "power semiconductor SiC GaN wide bandgap startup venture fund"
4. "semiconductor equipment materials startup investor portfolio"
5. "EDA chip design tools IP startup venture capital"
6. "advanced packaging chiplet heterogeneous integration startup VC"
7. "MEMS sensor semiconductor startup venture capital funding"
8. "RF millimeter wave 5G chip startup venture fund investment"
```

### 推荐 FIRM_DOMAINS

```python
# 产业 CVC & 专项 VC
"intelcapital.com",       # Intel Capital
"qualcommventures.com",   # Qualcomm Ventures
"nvidiaventures.com",     # NVIDIA Ventures (NVentures)
"applied.com",            # Applied Ventures (Applied Materials)
"samsung.com",            # Samsung Ventures
"amdfund.com",            # AMD Ventures (部分公开)
"tsmc.com",               # TSMC（战略投资参考）
"sequoiacap.com",
"walden.vc",              # Walden International (半导体专注)
"globalbrainvc.com",      # Global Brain (日本，半导体资源)
"crunchbase.com",
"pitchbook.com",
"semiengineering.com",    # 行业媒体参考
```

---

## 3. 医疗健康（医药 + 大健康）

| 属性 | 值 |
|------|----|
| **中文名** | 医疗健康（医药 + 大健康） |
| **英文名** | Healthcare & Life Sciences — Pharma, MedTech & Wellness |
| **领域代码** | `healthcare` |

### 子领域细分

| 子领域（中文） | 子领域（英文） | 代表方向 |
|--------------|--------------|---------|
| 创新药 | Novel Drug Development | 小分子、抗体、多肽、ADC |
| 细胞与基因治疗 | Cell & Gene Therapy | CAR-T、基因编辑、mRNA |
| AI 制药 | AI Drug Discovery | 靶点发现、分子生成、临床优化 |
| 医疗器械 | Medical Devices & MedTech | 手术机器人、植入器械、体外诊断 |
| 数字健康 | Digital Health | 慢病管理、健康 SaaS、患者平台 |
| 远程医疗 | Telehealth & Virtual Care | 在线问诊、远程监护、AI 问诊 |
| 精准医疗 / 基因组学 | Precision Medicine & Genomics | 液体活检、基因检测、伴随诊断 |
| 大健康 / 消费健康 | Consumer Health & Wellness | 营养、心理健康、可穿戴、健身科技 |
| CRO / CDMO | Contract Research & Manufacturing | 外包研究、生物制剂委托生产 |
| 医疗 AI 影像 | Medical AI Imaging | 放射影像 AI、病理 AI、眼科 AI |

### Exa 搜索查询组

```text
1. "healthcare AI venture capital firm investment portfolio digital health"
2. "biotech drug discovery series A venture fund startup"
3. "cell gene therapy startup venture capital funding"
4. "medical device MedTech venture capital investment thesis portfolio"
5. "AI drug discovery computational biology venture fund"
6. "precision medicine genomics liquid biopsy startup VC"
7. "digital health telehealth remote monitoring venture capital"
8. "consumer health mental wellness wearable startup venture fund"
```

### 推荐 FIRM_DOMAINS

```python
"rockhealth.com",         # Rock Health (数字健康专项)
"7wireventures.com",      # 7wire Ventures
"versantventures.com",    # Versant Ventures (biotech)
"atlasventure.com",       # Atlas Venture (biotech/pharma)
"orbimed.com",            # OrbiMed (医疗专项)
"sofinnova.com",          # Sofinnova Partners
"gv.com",                 # Google Ventures (大量医疗布局)
"a16zbio.com",            # a16z Bio (Andreessen bio fund)
"foresite.com",           # Foresite Capital
"danaherventures.com",    # Danaher Ventures
"healthcareventure.com",  # Healthcare Venture Associates
"crunchbase.com",
"pitchbook.com",
"fiercebiotech.com",      # 行业媒体参考
"statnews.com",           # 行业媒体参考
```

---

## 4. 教育科技

| 属性 | 值 |
|------|----|
| **中文名** | 教育科技 |
| **英文名** | Education Technology (EdTech) |
| **领域代码** | `edtech` |

### 子领域细分

| 子领域（中文） | 子领域（英文） | 代表方向 |
|--------------|--------------|---------|
| K-12 在线教育 | K-12 Online Education | 学科辅导、作业辅助、课后托管 |
| 高等教育科技 | Higher Education Tech | LMS、在线学位、MOOC 平台 |
| 职业技能培训 | Vocational & Skills Training | 编程、设计、职业认证 |
| 企业学习与培训 | Corporate Learning & Development | 企业 LMS、员工培训 SaaS |
| 语言学习 | Language Learning | AI 口语、沉浸式学习应用 |
| STEM / 编程教育 | STEM & Coding Education | 机器人教育套件、编程启蒙 |
| AI 自适应学习 | AI-Powered Adaptive Learning | 个性化学习路径、智能题库 |
| 教育评估与测试 | Assessment & Testing Tech | 自动评分、标准化考试平台 |

### Exa 搜索查询组

```text
1. "edtech education technology venture capital investment portfolio"
2. "AI tutoring adaptive personalized learning startup venture fund"
3. "K-12 online education startup series A venture capital"
4. "corporate learning LMS workforce training startup VC investment"
5. "higher education MOOC online degree startup venture capital"
6. "language learning AI edtech startup venture fund portfolio"
7. "STEM coding education startup venture capital"
8. "education assessment testing platform startup VC"
```

### 推荐 FIRM_DOMAINS

```python
"gsvventures.com",        # GSV Ventures (EdTech 专注)
"reach.capital",          # Reach Capital (K-12/EdTech)
"learn.capital",          # Learn Capital
"owl.vc",                 # Owl Ventures (EdTech 最大专项基金)
"newschoolsvc.org",       # NewSchools Venture Fund
"educate.online",         # Educate Online (参考)
"edsurge.com",            # EdSurge (行业媒体，含投资数据)
"holoniq.com",            # HolonIQ (EdTech 研究，含融资数据)
"crunchbase.com",
"pitchbook.com",
```

---

## 5. 金融科技与量化

| 属性 | 值 |
|------|----|
| **中文名** | 金融科技与量化 |
| **英文名** | Financial Technology & Quantitative Finance (FinTech & Quant) |
| **领域代码** | `fintech` |

### 子领域细分

| 子领域（中文） | 子领域（英文） | 代表方向 |
|--------------|--------------|---------|
| 支付与结算 | Payments & Settlement | 跨境支付、即时结算、POS 科技 |
| 借贷与信贷科技 | Lending & Credit Tech | 消费信贷、供应链金融、BNPL |
| 财富管理 / Robo-Advisory | Wealth Management & Robo-Advisory | 智能投顾、私人银行数字化 |
| 量化交易 / 算法交易 | Quantitative & Algorithmic Trading | 高频交易、因子投资、AI 策略 |
| 加密货币 / 区块链 | Crypto & Blockchain | DeFi、Layer 1/2、稳定币、Web3 基础设施 |
| 保险科技 | InsurTech | 嵌入式保险、AI 核保、参数保险 |
| 监管科技 | RegTech & Compliance | AML、KYC、风控合规自动化 |
| 嵌入式金融 / 开放银行 | Embedded Finance & Open Banking | Banking-as-a-Service、API 银行 |
| B2B 金融基础设施 | B2B Financial Infrastructure | 账户核心系统、清算系统、数据 API |

### Exa 搜索查询组

```text
1. "fintech venture capital investment firm portfolio payments digital banking"
2. "quantitative trading algorithmic finance AI startup venture capital"
3. "blockchain crypto web3 DeFi venture capital firm portfolio"
4. "insurtech insurance technology startup venture fund investment"
5. "regtech compliance financial crime anti-money laundering startup VC"
6. "embedded finance banking-as-a-service startup venture capital"
7. "wealth management robo-advisor wealthtech startup venture fund"
8. "B2B fintech infrastructure lending credit startup series A VC"
```

### 推荐 FIRM_DOMAINS

```python
"ribbitcap.com",          # Ribbit Capital (FinTech 专注)
"qed-investors.com",      # QED Investors (FinTech 专注)
"greenoaks.com",          # Greenoaks Capital
"a16z.com",               # a16z (Fintech + Crypto 重仓)
"paradigm.xyz",           # Paradigm (Crypto/Web3 专注)
"panteracapital.com",     # Pantera Capital (Crypto)
"union.vc",               # Union Square Ventures
"generalatlantic.com",    # General Atlantic
"fintechfutures.com",     # FinTech Futures (行业媒体)
"cbinsights.com",         # CB Insights (含融资数据)
"crunchbase.com",
"pitchbook.com",
```

---

## 6. 环保化工材料

| 属性 | 值 |
|------|----|
| **中文名** | 环保化工材料 |
| **英文名** | Green Chemistry, Advanced Materials & Environmental Tech |
| **领域代码** | `materials` |

### 子领域细分

| 子领域（中文） | 子领域（英文） | 代表方向 |
|--------------|--------------|---------|
| 绿色化工 | Green Chemistry & Sustainable Chemicals | 生物催化、无毒溶剂、绿色合成路线 |
| 先进材料 | Advanced Materials | 纳米材料、复合材料、超材料 |
| 生物基材料 | Bio-based Materials & Bioplastics | PLA、PHA、植物基包装 |
| 循环经济 / 回收 | Circular Economy & Recycling | 化学回收、废塑料再生、闭环供应链 |
| 碳捕集 / CCUS | Carbon Capture & Utilization (CCUS) | 直接空气捕集 DAC、CO₂ 矿化、工业碳捕集 |
| 水处理 / 净化 | Water Treatment & Purification | 膜技术、吸附材料、污水深度处理 |
| 环境修复 | Environmental Remediation | 土壤修复、重金属吸附、生物修复 |
| 功能涂层 / 特种化学品 | Functional Coatings & Specialty Chemicals | 防腐涂层、导热材料、电子化学品 |

### Exa 搜索查询组

```text
1. "advanced materials cleantech venture capital investment portfolio"
2. "sustainable green chemistry startup venture fund"
3. "bio-based materials bioplastics circular economy startup VC"
4. "carbon capture CCUS DAC direct air capture startup venture capital"
5. "specialty chemicals functional materials startup investment firm"
6. "water treatment purification environmental technology startup VC"
7. "nanomaterials composite advanced manufacturing startup venture fund"
8. "environmental remediation soil cleanup technology startup venture capital"
```

### 推荐 FIRM_DOMAINS

```python
"breakthrough.energy",    # Breakthrough Energy Ventures
"lowercarbon.cc",         # Lowercarbon Capital
"congruent.vc",           # Congruent Ventures (CleanTech)
"energyimpactvp.com",     # Energy Impact Partners
"startupenergy.de",       # StartupEnergy Transition (参考)
"cleantech.com",          # Cleantech Group (研究+投资数据)
"ycombinator.com",        # YC（部分 CleanTech 项目）
"crunchbase.com",
"pitchbook.com",
"chemistryworld.com",     # 行业媒体参考
```

---

## 7. 能源与新能源

| 属性 | 值 |
|------|----|
| **中文名** | 能源与新能源 |
| **英文名** | Energy & Clean Energy Transition |
| **领域代码** | `energy` |

### 子领域细分

| 子领域（中文） | 子领域（英文） | 代表方向 |
|--------------|--------------|---------|
| 太阳能 | Solar Energy | 光伏组件、钙钛矿电池、CSP |
| 风能 | Wind Energy | 陆上风电、海上风电、风机设计 |
| 储能 / 电池 | Energy Storage & Battery | 锂电池、固态电池、液流电池、BESS |
| 氢能 / 燃料电池 | Green Hydrogen & Fuel Cells | 电解槽、PEM 燃料电池、氢储运 |
| 核能（SMR） | Nuclear & Small Modular Reactors | 小型模块堆、聚变能（Fusion） |
| 智能电网 | Smart Grid & Grid Tech | 配电自动化、需求响应、虚拟电厂 |
| 能源管理 / 优化 | Energy Management & Optimization | 工业节能、楼宇能源管理（BMS）、AI 优化 |
| EV 充电基础设施 | EV Charging Infrastructure | 快充桩、V2G、充电网络运营 |
| 生物质 / 生物燃料 | Biomass & Biofuels | 航空生物燃料（SAF）、沼气、生物柴油 |

### Exa 搜索查询组

```text
1. "clean energy venture capital investment firm portfolio renewable"
2. "battery energy storage startup venture fund series A"
3. "solar energy photovoltaic perovskite startup venture capital"
4. "green hydrogen electrolysis fuel cell startup VC investment"
5. "nuclear SMR small modular reactor fusion startup venture fund"
6. "smart grid virtual power plant demand response startup VC"
7. "EV charging electric vehicle infrastructure startup venture capital"
8. "energy transition climate tech deep tech venture capital firm"
```

### 推荐 FIRM_DOMAINS

```python
"breakthrough.energy",    # Breakthrough Energy Ventures (Bill Gates)
"lowercarbon.cc",         # Lowercarbon Capital
"energyimpactvp.com",     # Energy Impact Partners
"congruent.vc",           # Congruent Ventures
"prelude.vc",             # Prelude Ventures (CleanTech)
"sherwoodvc.com",         # Sherwood Ventures
"powerhouse.fund",        # Powerhouse Ventures (能源创新)
"cleantech.com",          # Cleantech Group
"bloombergnef.com",       # BloombergNEF (行业数据参考)
"crunchbase.com",
"pitchbook.com",
```

---

## 附录：多领域 FIRM_DOMAINS 合并参考

以下域名跨多个领域通用，建议放入所有领域的 `include_domains` 基础列表：

```python
BASE_FIRM_DOMAINS = [
    # 综合融资数据库
    "crunchbase.com",
    "pitchbook.com",
    "dealroom.co",
    "tracxn.com",
    # 综合 VC 官网
    "sequoiacap.com",
    "a16z.com",
    "gv.com",
    "generalatlantic.com",
    "lightspeedvp.com",
    "accel.com",
    "linkedin.com",          # VC 人员/基金页面
]
```

---

## 版本记录

| 日期 | 变更内容 | 作者 |
|------|---------|------|
| 2026-04-30 | 初始版本，含 7 个领域、4 个投资阶段 | — |
