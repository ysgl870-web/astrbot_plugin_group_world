# V1.9.2

- 云端玩法分区重做为更接近 AstrBot Plugin Page/配置文档风格的独立云端配置中心。
- 使用网站 API Key 获取 ysgl 云端已审核商品目录，并支持按商品名、编号、作者搜索。
- 社区 JSON 支持按上传用户、分类、关键词筛选，可同时选择多个数据包。
- 保存 JSON 选择后立即同步，选中包中的 products / bosses / monsters / npcs / tutorials 会合并到对应云端数据目录。
- 新增 ysgl 网站公告读取区，展示站点公告、公告等级、发布时间，并支持手动刷新。
- 新增当前生效 JSON 合并统计。

# V1.9.1

- 修复后台“导入数据”“导出数据”按钮未绑定点击事件的问题。
- 导入按钮现在会正确打开 JSON 导入弹窗。
- 导出按钮现在通过 AstrBot Plugin Page Bridge 下载 JSON 快照。
- 保留 V1.9.0 云端审核、多选社区 JSON、YSGL 云端连接功能。
