# 评测接口（通用dify api方式调用，直接可用）

```
curl --location 'http://localhost:5001/v1/datasets/19bb05f4-f021-4b51-bd10-1eb7100e2396/retrieve' \
--header 'Authorization: Bearer dataset-4bMt0lbU4LEQ9IAuPlfx49e2' \
--header 'Content-Type: application/json' \
--data '
{"query":"评分","attachment_ids":[],"retrieval_model":{"search_method":"hybrid_search","reranking_enable":true,"reranking_mode":"reranking_model","reranking_model":{"reranking_provider_name":"langgenius/tongyi/tongyi","reranking_model_name":"qwen3-rerank"},"weights":{"weight_type":"customized","keyword_setting":{"keyword_weight":0.3},"vector_setting":{"vector_weight":0.7,"embedding_model_name":"","embedding_provider_name":""}},"top_k":10,"score_threshold_enabled":true,"score_threshold":0.55}}
'
```

# 返回结果参考

```
{
    "query": {
        "content": "\u8bc4\u5206"
    },
    "records": [
        {
            "segment": {
                "id": "e6e1a6cc-21ed-40e2-b302-68cd228dd7f2",
                "position": 5,
                "document_id": "fce211b2-4ff1-4344-a127-99fc8d5ebdc3",
                "content": "\u8bc4\u5206\u5361\u662f\u6574\u6761\u51b3\u7b56\u94fe\u8def\u7684\u7b2c\u4e00\u9053\u95f8\u95e8\uff0c\u5b83\u51b3\u5b9a\u4e86\u7edd\u5927\u591a\u6570\u7533\u8bf7\u7684\u8d70\u5411\u3002\u8bbe\u8ba1\u4e0a\u6211\u4eec\u523b\u610f\u8ba9\u81ea\u52a8\u901a\u8fc7\u4e0e\u81ea\u52a8\u62d2\u7edd\u4e4b\u95f4\u7559\u51fa\u4e00\u6bb5\u7070\u533a\uff0c\u7070\u533a\u5185\u7684\u7533\u8bf7\u4ea4\u7531\u89c4\u5219\u5f15\u64ce\u548c\u4eba\u5de5\u5171\u540c\u5904\u7406\u3002\u7070\u533a\u8fc7\u7a84\u4f1a\u8ba9\u901a\u8fc7\u7387\u5267\u70c8\u6ce2\u52a8\uff0c\u8fc7\u5bbd\u5219\u4f1a\u628a\u6210\u672c\u538b\u5728\u4eba\u5de5\u4fa7\uff0c\u8fd9\u4e2a\u5e73\u8861\u9700\u8981\u6309\u5b63\u5ea6\u56de\u770b\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 116,
                "tokens": 250,
                "keywords": [],
                "index_node_id": "7357518a-3b75-42fa-b3a3-e8d5c21cb534",
                "index_node_hash": "f923ef557dfa97e22231fb4a6839881414b598dc649420c85703cd64c8117c08",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785803658,
                "indexing_at": 1785803659,
                "completed_at": 1785803678,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "fce211b2-4ff1-4344-a127-99fc8d5ebdc3",
                    "data_source_type": "upload_file",
                    "name": "B4-risk-qihe.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "0415ac9f-bdd2-4331-9709-b1023e991e7d",
                    "content": "\u8bc4\u5206\u5361\u662f\u6574\u6761\u51b3\u7b56\u94fe\u8def\u7684\u7b2c\u4e00\u9053\u95f8\u95e8\uff0c\u5b83\u51b3\u5b9a\u4e86\u7edd\u5927\u591a\u6570\u7533\u8bf7\u7684\u8d70\u5411\u3002\u8bbe\u8ba1\u4e0a\u6211\u4eec\u523b\u610f\u8ba9\u81ea\u52a8\u901a\u8fc7\u4e0e\u81ea\u52a8\u62d2\u7edd\u4e4b\u95f4\u7559\u51fa\u4e00\u6bb5\u7070\u533a\uff0c\u7070\u533a\u5185\u7684\u7533\u8bf7\u4ea4\u7531\u89c4\u5219\u5f15\u64ce\u548c\u4eba\u5de5\u5171\u540c\u5904\u7406\u3002\u7070\u533a\u8fc7\u7a84\u4f1a\u8ba9\u901a\u8fc7\u7387\u5267\u70c8\u6ce2\u52a8\uff0c\u8fc7\u5bbd\u5219\u4f1a\u628a\u6210\u672c\u538b\u5728\u4eba\u5de5\u4fa7\uff0c\u8fd9\u4e2a\u5e73\u8861\u9700\u8981\u6309\u5b63\u5ea6\u56de\u770b\u3002",
                    "position": 1,
                    "score": 0.6235474253416738
                }
            ],
            "score": 0.6235474253416738,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "055cbc3f-7351-45b3-83a6-14bbbcec9ad4",
                "position": 4,
                "document_id": "743a8d77-a63f-4593-adf3-e23038d3372e",
                "content": "2. \u8bc4\u5206\u5361\u4e0e\u51c6\u5165\n\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 620 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 43,
                "tokens": 82,
                "keywords": [],
                "index_node_id": "73ad3cdc-2208-40d5-a0ce-b76eb0790eb2",
                "index_node_hash": "60389c9d3a58ed1fdaa1fdbcf5b1241159781275fd6a743162916de5263870da",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785803598,
                "indexing_at": 1785803599,
                "completed_at": 1785803618,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "743a8d77-a63f-4593-adf3-e23038d3372e",
                    "data_source_type": "upload_file",
                    "name": "B5-risk-group.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "6af546e8-b974-4a97-aeed-06e6f786a0e1",
                    "content": "\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 620 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                    "position": 2,
                    "score": 0.6209552936618506
                }
            ],
            "score": 0.6209552936618506,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "fb37017d-5f3f-499b-a2b6-d7d880739a28",
                "position": 4,
                "document_id": "ed5aa6c9-4d4a-4b96-8eb7-f5bb0f6ece0f",
                "content": "2. \u8bc4\u5206\u5361\u4e0e\u51c6\u5165\n\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 630 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 43,
                "tokens": 82,
                "keywords": [],
                "index_node_id": "6c16cc5d-c195-47b2-82a5-0c085db01ce6",
                "index_node_hash": "94a7d6744b93ec873759901667d17c24fefc54079a87f3dd22cd87b9c2c9c060",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785803638,
                "indexing_at": 1785803639,
                "completed_at": 1785803658,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "ed5aa6c9-4d4a-4b96-8eb7-f5bb0f6ece0f",
                    "data_source_type": "upload_file",
                    "name": "B3-risk-nanxu.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "8fc93a89-eb76-45f3-8cc6-7104ee949b3b",
                    "content": "\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 630 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                    "position": 2,
                    "score": 0.6195500449633474
                }
            ],
            "score": 0.6195500449633474,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "3eec3b2b-2e1e-425c-9549-d3206f3a88cf",
                "position": 4,
                "document_id": "a84c3a43-d3ba-4c48-816a-317320c5d518",
                "content": "2. \u8bc4\u5206\u5361\u4e0e\u51c6\u5165\n\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 612 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 43,
                "tokens": 83,
                "keywords": [],
                "index_node_id": "5bad2329-b268-4177-b0e4-90ac52bfea0c",
                "index_node_hash": "79fe8bf24285c251bde0d5374f342095382e5d0147c6b9b271496989f5a5388b",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785803618,
                "indexing_at": 1785803619,
                "completed_at": 1785803623,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "a84c3a43-d3ba-4c48-816a-317320c5d518",
                    "data_source_type": "upload_file",
                    "name": "B1-risk-hongyuan.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "3ef3ea15-c730-4f5e-9aba-b89fd8c41e55",
                    "content": "\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 612 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                    "position": 2,
                    "score": 0.619256796592153
                }
            ],
            "score": 0.619256796592153,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "e8ea6fcc-873a-47c5-bdec-9c0ba18ea7cb",
                "position": 4,
                "document_id": "56d9470b-0a3e-4612-a52c-9d081ca6fc5f",
                "content": "2. \u8bc4\u5206\u5361\u4e0e\u51c6\u5165\n\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 648 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 43,
                "tokens": 83,
                "keywords": [],
                "index_node_id": "8419e906-8984-449f-a954-09e939b404b7",
                "index_node_hash": "f596fe7456e6df71e5e44f8a1315592840156f4f8d81709fb87a72e9df7781af",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785803579,
                "indexing_at": 1785803580,
                "completed_at": 1785803591,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "56d9470b-0a3e-4612-a52c-9d081ca6fc5f",
                    "data_source_type": "upload_file",
                    "name": "B2-risk-jinzhou.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "185b6ca2-3058-4fae-ab43-64891c619100",
                    "content": "\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 648 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                    "position": 2,
                    "score": 0.619195620177432
                }
            ],
            "score": 0.619195620177432,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "6814c644-d202-4852-aa21-cad5d06a12fc",
                "position": 4,
                "document_id": "fce211b2-4ff1-4344-a127-99fc8d5ebdc3",
                "content": "2. \u8bc4\u5206\u5361\u4e0e\u51c6\u5165\n\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 605 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 43,
                "tokens": 83,
                "keywords": [],
                "index_node_id": "5dc7ff7f-fdda-41cd-aa71-9b8367f8a329",
                "index_node_hash": "178a4a48d5c8ad81d469451b49aace5588203f901e94e6696017698f71c540ae",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785803658,
                "indexing_at": 1785803659,
                "completed_at": 1785803678,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "fce211b2-4ff1-4344-a127-99fc8d5ebdc3",
                    "data_source_type": "upload_file",
                    "name": "B4-risk-qihe.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "cfed8af8-cc31-4707-89e1-801756268f01",
                    "content": "2. \u8bc4\u5206\u5361\u4e0e\u51c6\u5165",
                    "position": 1,
                    "score": 0.6178737916493392
                },
                {
                    "id": "9564046c-685d-4b6d-95f1-ca7138de8546",
                    "content": "\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 605 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                    "position": 2,
                    "score": 0.6096711345271001
                }
            ],
            "score": 0.6178737916493392,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "710425ca-565e-442e-8de1-cf1104ae3104",
                "position": 11,
                "document_id": "56d9470b-0a3e-4612-a52c-9d081ca6fc5f",
                "content": "3. \u62d2\u7edd\u7b56\u7565\n\u4f4e\u4e8e 520 \u5206 \u7684\u7533\u8bf7\u76f4\u63a5\u81ea\u52a8\u62d2\u7edd\uff0c\u4e0d\u518d\u8fdb\u5165\u4eba\u5de5\u73af\u8282\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 36,
                "tokens": 65,
                "keywords": [],
                "index_node_id": "5dd4a425-6097-45d2-a33e-afe030993b0c",
                "index_node_hash": "6e9661e3c56420fc0a778cc55d04046f765ca0b5545d1c47b1f2f177fbafff21",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785803579,
                "indexing_at": 1785803580,
                "completed_at": 1785803592,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "56d9470b-0a3e-4612-a52c-9d081ca6fc5f",
                    "data_source_type": "upload_file",
                    "name": "B2-risk-jinzhou.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [
                {
                    "id": "5c46b5ac-dbf7-4fa6-9e2b-69b0bc7a1589",
                    "content": "\u4f4e\u4e8e 520 \u5206 \u7684\u7533\u8bf7\u76f4\u63a5\u81ea\u52a8\u62d2\u7edd\uff0c\u4e0d\u518d\u8fdb\u5165\u4eba\u5de5\u73af\u8282\u3002",
                    "position": 2,
                    "score": 0.6104877868707004
                }
            ],
            "score": 0.6104877868707004,
            "tsne_position": null,
            "files": [],
            "summary": null
        }
    ]
}

```



