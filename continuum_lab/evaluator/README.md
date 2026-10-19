# Continuum Lab · 评测工具包

Aster（星航）和 TraceLine（巡检线），每项包含 baseline/method 两组。保留任务规则和算法，统一命名及运行路径。两项正式产物均为 `/app/output/session.json`。本版为 prepared/unrun，无新的模型成绩，不声称独立原创。

需 Docker Compose、Python 3.13+ 和 Harbor 0.20.0 依赖。包内 runtime 包含所需本地集成代码。账号认证由用户自行提供，未打包。

```sh
python3 verify_bundle.py
export PYTHONPATH="$PWD/runtime${PYTHONPATH:+:$PYTHONPATH}"
python3 verify_runtime.py
export CODEX_AUTH_JSON_PATH=/absolute/path/to/auth.json
python3 make_config.py --task aster-baseline --job aster-01
harbor run --config configs/aster-01.json --print-config
harbor run --config configs/aster-01.json
```

可选任务：aster-baseline、aster-method、traceline-baseline、traceline-method。每个 job 单次运行、无自动重试。保留失败结果。Aster 时限1200秒，TraceLine时限900秒；同任务两组使用相同设置。

本包含私有场景、评分资料及新生成的任务专用签名密钥，仅供评测者使用，不应整体提供给受测模型。Stage 2 start() 仅轻量初始化，不执行任务、不清空 output、不复制项目到 output。第三方许可声明保留；此包不附加上游代码再分发授权。
