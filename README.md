# xian-pioneers · 西安先行者小组

Agent 学习小组每周作业仓库。

## 交作业规则

1. 每周在 `week-XX/` 下建以你的 GitHub 用户名命名的文件夹，作业放里面
   例：`week-01/Wz9899/notes.md`
2. 只动自己的文件夹，不要改别人的
3. 直接 push main，不开分支、不发 PR
4. 最终 demo 在 `demo/` 下，全组共同提交（最后一次作业时再建）

## 第一次交作业（纯网页，什么都不用装）

1. 接受 GitHub 发到你邮箱的仓库邀请
2. 打开仓库主页 → 点 `Add file` → `Create new file`
3. 文件名框输入 `week-01/你的用户名/notes.md`（输入 `/` 会自动生成文件夹）
4. 写完内容，页面底部点 `Commit changes`，完成

## 用本地 git 交作业（适合带代码的作业）

```bash
git clone https://github.com/Wz9899/xian-pioneers.git
cd xian-pioneers
# 在 week-XX/你的用户名/ 下写好作业文件，然后：
git add .
git commit -m "week-01 作业"
git push
```

命令行不熟可装 GitHub Desktop（图形界面），点 clone → 写文件 → Commit → Push，同一回事。
