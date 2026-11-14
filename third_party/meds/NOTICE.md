# 第三方组件说明

## MEDS（Medical Event Data Standard）

- 上游仓库：https://github.com/Medical-Event-Data-Standard/meds
- 许可：Apache License 2.0（见本目录 `LICENSE`）
- 本仓库包含的内容：`src/meds/schema.py` 一份原样副本（未修改），
  以及 `governance/meds.py` 在运行期通过 `importlib` 加载它做产物校验。
- 版本：随课程实验使用的 `meds` 0.4（`dataset.json` 中 `meds_version` 记录为 `0.4`）。
- 为什么不用 pip 安装：课程环境离线安装第三方包不稳定，且只需要 schema 定义；
  副本可保证"校验口径"与实验时的版本一致。
- 升级方式：用上游同路径文件替换 `src/meds/schema.py`，同步检查
  `governance/materialize.py` 与 `tests/test_meds.py` 是否仍通过。

除上述文件外，`code_repo/` 内的代码均为本组原创。
