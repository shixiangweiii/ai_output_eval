# 评测接口（通用dify api方式调用，直接可用）

```
curl --location 'http://localhost:5001/v1/datasets/c06737dc-5b94-4eab-9f47-0f36113f0ced/retrieve' \
--header 'Authorization: Bearer dataset-4bMt0lbU4LEQ9IAuPlfx49e2' \
--header 'Content-Type: application/json' \
--data '

{
    "query": "测试",
    "attachment_ids": [],
    "retrieval_model": {
        "search_method": "semantic_search",
        "reranking_enable": true,
        "reranking_mode": null,
        "reranking_model": {
            "reranking_provider_name": "langgenius/tongyi/tongyi",
            "reranking_model_name": "qwen3-rerank"
        },
        "weights": null,
        "top_k": 5,
        "score_threshold_enabled": true,
        "score_threshold": 0.55
    }
}

'
```

# 返回结果参考

```
{
    "query": {
        "content": "测试"
    },
    "records": [
        {
            "segment": {
                "id": "cd1b034c-72c3-4bda-b48d-c4d935602832",
                "position": 1,
                "document_id": "18d51651-40f3-4b12-aff8-6aa260e17e0e",
                "content": "协同文档 SaaS \"云笺\" 多人实时协作功能 PRD\n文档状态：评审通过（Reviewed）\n版本：V2.1（**注意：V1.x 的冲突处理方案已废弃，见第 5 节**）\n产品经理：赵一帆\n涉及团队：前端、后端、实时通信、测试",
                "sign_content": null,
                "answer": null,
                "word_count": 114,
                "tokens": 186,
                "keywords": [],
                "index_node_id": "ad24cd2d-f405-4992-87f0-917ea30a4950",
                "index_node_hash": "aa94bca5595426543a3e1a5cd00b72181a2cc70e43e85c16f701c2fbf3b719dc",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785771396,
                "indexing_at": 1785771397,
                "completed_at": 1785771409,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "18d51651-40f3-4b12-aff8-6aa260e17e0e",
                    "data_source_type": "upload_file",
                    "name": "10_协同文档SaaS云笺多人实时协作功能PRD.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "a110b11b-4bea-42ac-8f90-c5b81f06d01d",
                    "content": "涉及团队：前端、后端、实时通信、测试",
                    "position": 5,
                    "score": 0.5624200118209163
                }
            ],
            "score": 0.5624200118209163,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "8055f07f-b6a8-4cbf-805c-02adaf10a6ec",
                "position": 21,
                "document_id": "18d51651-40f3-4b12-aff8-6aa260e17e0e",
                "content": "6. 验收标准与埋点\n功能验收（对齐第一节北极星指标）：",
                "sign_content": null,
                "answer": null,
                "word_count": 28,
                "tokens": 64,
                "keywords": [],
                "index_node_id": "8f819b74-517c-4a25-8c0b-a1f96b6111e0",
                "index_node_hash": "80c69f205e186ebff293f5bec435b739391266549b36ba297f869b8c7f6e675e",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785771396,
                "indexing_at": 1785771397,
                "completed_at": 1785771409,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "18d51651-40f3-4b12-aff8-6aa260e17e0e",
                    "data_source_type": "upload_file",
                    "name": "10_协同文档SaaS云笺多人实时协作功能PRD.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "636e61d9-c39c-4680-99e2-030610a06c84",
                    "content": "功能验收（对齐第一节北极星指标）：",
                    "position": 2,
                    "score": 0.5538809499991034
                }
            ],
            "score": 0.5538809499991034,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "d38fdb32-73a8-4c7a-b60b-d2d76c29e9d8",
                "position": 17,
                "document_id": "4a5fc634-dd90-4ebb-83b4-d5271c98c294",
                "content": "6. 效果数据与诚实的辨析\n前面第一节提到\"正确率提升\"，具体数字：使用系统的班级，单元测试平均正确率比对照班高 14%。",
                "sign_content": null,
                "answer": null,
                "word_count": 61,
                "tokens": 129,
                "keywords": [],
                "index_node_id": "da6c6d04-3499-4f8a-a00b-93470895e4b9",
                "index_node_hash": "0244325a60ed941d7e7ddd7df1253d9dce2c4d7fc6fb3fd85896245c4fffe983",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785771385,
                "indexing_at": 1785771386,
                "completed_at": 1785771388,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "4a5fc634-dd90-4ebb-83b4-d5271c98c294",
                    "data_source_type": "upload_file",
                    "name": "09_在线教育平台自适应学习系统技术白皮书.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "0a4a96b7-a0b6-41d9-b7e2-033d91474a19",
                    "content": "前面第一节提到\"正确率提升\"，具体数字：使用系统的班级，单元测试平均正确率比对照班高 14%。",
                    "position": 2,
                    "score": 0.5465549365100756
                }
            ],
            "score": 0.5465549365100756,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "62a8d525-02e2-408c-88ff-29f7297d9754",
                "position": 22,
                "document_id": "4a5fc634-dd90-4ebb-83b4-d5271c98c294",
                "content": "我们的解法分两步。第一步，用一套精心设计的\"摸底测验\"快速探测，题目按知识图谱的关键节点分布，用尽量少的题（十几道）覆盖尽量多的先修链，快速给出一个粗略的能力画像。第二步，在数据积累起来之前，先借用\"同类人群的平均掌握度\"作为先验（比如同年级、同区域学生的典型水平），随着该学生答题增多，再逐渐从\"群体先验\"过渡到\"个人真实估计\"。",
                "sign_content": null,
                "answer": null,
                "word_count": 166,
                "tokens": 358,
                "keywords": [],
                "index_node_id": "163b2516-80b1-4c92-bce7-473548411ab8",
                "index_node_hash": "4af79e655b6b1c7197e117128b759257bd841f3e34bf377852f5ae1ef01d962e",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785771385,
                "indexing_at": 1785771386,
                "completed_at": 1785771392,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "4a5fc634-dd90-4ebb-83b4-d5271c98c294",
                    "data_source_type": "upload_file",
                    "name": "09_在线教育平台自适应学习系统技术白皮书.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "50847830-94c8-4d63-ba69-cd7833b18317",
                    "content": "我们的解法分两步。第一步，用一套精心设计的\"摸底测验\"快速探测，题目按知识图谱的关键节点分布，用尽量少的题（十几道）覆盖尽量多的先修链，快速给出一个粗略的能力画像。第二步，在数据积累起来之前，先借用\"同类人群的平均掌握度\"作为先验（比如同年级、同区域学生的典型水平），随着该学生答题增多，再逐渐从\"群体先验\"过渡到\"个人真实估计\"。",
                    "position": 1,
                    "score": 0.5432799069675023
                }
            ],
            "score": 0.5432799069675023,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "e561caea-6dab-477a-b7af-7422546db85e",
                "position": 19,
                "document_id": "4a5fc634-dd90-4ebb-83b4-d5271c98c294",
                "content": "补一句评估口径：判断掌握度模型判得准不准，我们同样用**精确率和召回率**——精确率高代表被判为\"已掌握\"的知识点大多真掌握了，召回率高代表真正掌握的点没被漏判成没掌握。这和风控、量化里用的其实是同一套评估语言，只是换了业务含义；跨行业迁移方法论时，先对齐指标的业务定义，比直接搬公式更重要。",
                "sign_content": null,
                "answer": null,
                "word_count": 146,
                "tokens": 312,
                "keywords": [],
                "index_node_id": "dc153c27-018e-4773-8aae-9a6d97cc4a43",
                "index_node_hash": "2d1db77624003ae4fd31f9a48b9a02c61a6c736f10e8146c31b5cf55d3866eda",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785771385,
                "indexing_at": 1785771386,
                "completed_at": 1785771391,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "4a5fc634-dd90-4ebb-83b4-d5271c98c294",
                    "data_source_type": "upload_file",
                    "name": "09_在线教育平台自适应学习系统技术白皮书.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "839a8689-bf1c-4cf1-a2d2-bb770199a7e1",
                    "content": "补一句评估口径：判断掌握度模型判得准不准，我们同样用**精确率和召回率**——精确率高代表被判为\"已掌握\"的知识点大多真掌握了，召回率高代表真正掌握的点没被漏判成没掌握。这和风控、量化里用的其实是同一套评估语言，只是换了业务含义；跨行业迁移方法论时，先对齐指标的业务定义，比直接搬公式更重要。",
                    "position": 1,
                    "score": 0.37384649044772744
                }
            ],
            "score": 0.37384649044772744,
            "tsne_position": null,
            "files": [],
            "summary": null
        }
    ]
}
```



