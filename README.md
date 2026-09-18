# OpenICow 下载

这个公开仓库仅用于发布 OpenICow 安装包镜像和下载校验工具，不包含应用源码。

- 官网：<https://openicow.com/>
- GitHub 下载：<https://github.com/lureyu6/openicow-downloads/releases>

当前索引为空，安装包将在官网对应版本完成发布后加入。平台、签名和功能限制以官网说明及对应版本说明为准。

## 镜像流程

私有发布流程将已上线的安装包写入 `release-index.json`。推送该文件到 `main` 后，GitHub Actions 从官网获取文件，逐一核对字节数和 SHA-256，再用此仓库的 `GITHUB_TOKEN` 发布 GitHub Release。普通 README 或脚本修改不会自动发布安装包。

只接受 `https://openicow.com/downloads/<version>/<name>`，拒绝重定向。下载文件只是安装包资产，不会在工作流中执行。已发布版本的名称、字节数或摘要与索引不一致时直接停止，不覆盖现有公开安装包；中断后留下的草稿可以重试。

手动运行 **Mirror verified downloads**，默认模式为 `check`：只运行行为测试与索引格式检查，不下载或发布安装包。明确选择 `publish` 才执行镜像。

```bash
python3 -m unittest discover -s tests
python3 scripts/mirror.py --mode check
```

索引格式示例（摘要应填入实际 64 位小写十六进制值）：

```json
{
  "schemaVersion": 1,
  "releases": [{
    "tag": "openicow-v0.3.0-preview.2",
    "version": "0.3.0-preview.2",
    "assets": [{
      "name": "OpenICow-0.3.0-preview.2-macOS-arm64.dmg",
      "url": "https://openicow.com/downloads/0.3.0-preview.2/OpenICow-0.3.0-preview.2-macOS-arm64.dmg",
      "bytes": 748199485,
      "sha256": "426657bc217729f1d088704436e8a870ac36f96c68be567c5e0cc26c33d0f6bd"
    }]
  }]
}
```
