"""生成供应商适配层。

一个供应商 = 一个模块（`provider` / `label` / `default_base_url` / `kinds` / `adapter` / `models`），
`app.generation.registry` 会自动发现本包内的模块，因此**接入新供应商只需要在这里新增一个文件**。

模块需要暴露的约定见 `app/generation/registry.py` 顶部说明。
"""
