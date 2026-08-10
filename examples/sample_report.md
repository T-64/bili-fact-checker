# B站口播事实核查报告

**视频**: [示例：科普口播核查演示](https://www.bilibili.com/video/BV1EXAMPLE001)
**BV**: `BV1EXAMPLE001` · 字幕来源: `cc`
**生成时间**: 2026-08-10T00:00:00+00:00

> 本工具输出仅为辅助线索，不是权威最终裁决。请核对来源链接；标为 model_inference 的条目无外部举证。

## 内容总结

**概述**
这是一份示例报告，展示 bili-fact-checker 的输出结构。

**关键断言**
视频提出若干可核查声明，系统分别检索 ClaimReview 与网页证据。

**如何阅读**
优先看带 sourced_* 标签的条目；model_inference 仅供参考。

## 声明核查（2）

汇总：有出处 1 · 模型推断 1 · 弱/未核实 1

### 1. [其他] `supported` · `sourced_web`
**声明**: 示例声明：水在标准大气压下约 100°C 沸腾
**EN**: Water boils at about 100C at standard atmospheric pressure
**时间**: 42s
**理由**: 网页证据与声明一致（示例数据）。
**来源**:
- https://example.com/boiling-point

### 2. [统计数据] `unverified` · `model_inference`
**声明**: 示例声明：某冷门统计无法在公开库命中
**EN**: An obscure statistic with no public fact-check hit
**时间**: 90s
**理由**: 无外部证据，仅为模型推断（示例）。
**来源**: （无外部证据 · model_inference）
