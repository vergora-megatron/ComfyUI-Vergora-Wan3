# Releasing to the Comfy Registry

Once published, this package can be installed straight from ComfyUI-Manager.

## One-time setup

1. Create an account at <https://registry.comfy.org> and create a **publisher**. The publisher id
   (the part after `@` on your profile page) must match `PublisherId` in `pyproject.toml`, which is
   currently `levi-li`. It is globally unique and cannot be changed later.
2. Create a **Registry Publishing API key** for that publisher (your publisher page → create key).
   The key is shown once; store it somewhere safe.

## Before every release

1. Bump `version` in `pyproject.toml` (semantic versioning) and add the matching entry to
   `CHANGELOG.md`.
2. Check that `Icon` still points at a reachable image (SVG, PNG, JPG or GIF, max 800x400 px). The
   current value points at `icon.svg` in this repository.
3. Run the offline test: `python test_offline.py`.
4. Commit and push — the registry reads the repository, and the archive is built from git-tracked
   files.
5. Create an annotated tag named after the package version, for example `v0.4.1`, and push it:

   ```bash
   git tag -a v0.4.1 -m "ComfyUI-Vergora 0.4.1"
   git push origin v0.4.1
   ```

   Tags use `vMAJOR.MINOR.PATCH`. Do not move or recreate a tag after publication; release a new
   patch version instead.

## Publish

```bash
pip install comfy-cli
comfy node publish      # run inside this folder; it asks for the API key the first time
```

Because the archive is built from git-tracked files, anything that should stay out of it (tests,
large samples, design assets) belongs in a `.comfyignore` file at the repository root; the syntax
matches `.gitignore`.

## Alternative: GitHub Actions

`comfy-cli` ships a publish action, so a tag push can publish for you — see the "Publishing Nodes"
page in the ComfyUI documentation for the workflow file.

## 中文速记

一次性：在 registry.comfy.org 注册 → 创建 publisher（id 必须与 `pyproject.toml` 的 `PublisherId`
一致，当前是 `levi-li`）→ 生成发布用 API key。

每次发布：改版本号 + 记 CHANGELOG → `python test_offline.py` → 提交推送 → 创建并推送
`v主版本.次版本.修订版本` tag → 在本目录执行 `comfy node publish`（首次会问 API key）。

Registry 节点 ID 为 `comfyui-vergora-wan3`，显示名为 `ComfyUI-Vergora`，发布者 ID 为
`levi-li`。节点 ID 是永久安装标识，不应跟随显示名修改。
