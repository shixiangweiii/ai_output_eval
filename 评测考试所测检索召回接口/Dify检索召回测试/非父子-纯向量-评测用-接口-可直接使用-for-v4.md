# 评测接口（通用dify api方式调用，直接可用）

```
curl --location 'http://localhost:5001/v1/datasets/e4ae9a0d-710a-4d50-a65f-5d847db1d83a/retrieve' \
--header 'Authorization: Bearer dataset-4bMt0lbU4LEQ9IAuPlfx49e2' \
--header 'Content-Type: application/json' \
--data '
{
    "query": "评分",
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
        "content": "\u8bc4\u5206"
    },
    "records": [
        {
            "segment": {
                "id": "d9a9f9e6-9ed7-48a1-bacb-ee84fde246da",
                "position": 7,
                "document_id": "8829bc19-3548-4a4b-9528-da82036134a2",
                "content": "\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 630 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 33,
                "tokens": 66,
                "keywords": [],
                "index_node_id": "af3217e1-8eec-4886-b865-15e2bfdee373",
                "index_node_hash": "bd915e697aa37222356d07f1ce88f5e5d042dea682522a35a49949f23900ff22",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785799536,
                "indexing_at": 1785799536,
                "completed_at": 1785799539,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "8829bc19-3548-4a4b-9528-da82036134a2",
                    "data_source_type": "upload_file",
                    "name": "B3-risk-nanxu.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [],
            "score": 0.6195500661120099,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "117a2f92-17cb-4d65-9d76-94fb49ab2c5e",
                "position": 7,
                "document_id": "816a4b21-8a50-4079-b3ed-7e0decaaf224",
                "content": "\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 612 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 33,
                "tokens": 67,
                "keywords": [],
                "index_node_id": "34a454ae-5d85-48bc-b34d-40e7e99b40c6",
                "index_node_hash": "87715456d26aa2d5cc5490971d2e529b35113510a7f234592ec39a4848e0a8f4",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785799527,
                "indexing_at": 1785799527,
                "completed_at": 1785799532,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "816a4b21-8a50-4079-b3ed-7e0decaaf224",
                    "data_source_type": "upload_file",
                    "name": "B1-risk-hongyuan.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [],
            "score": 0.619256796592153,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "d1f585e4-c441-45a4-853d-393506dbacee",
                "position": 7,
                "document_id": "ce96090c-926b-4eb0-8f0f-994f215b2db4",
                "content": "\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 648 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 33,
                "tokens": 67,
                "keywords": [],
                "index_node_id": "7c20f406-9373-4183-ba84-8c800ba34a0f",
                "index_node_hash": "29fed427dd5b229567db9d8d39561e1c186abd96c5dd832ca9393a4731fe5685",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785799543,
                "indexing_at": 1785799543,
                "completed_at": 1785799545,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "ce96090c-926b-4eb0-8f0f-994f215b2db4",
                    "data_source_type": "upload_file",
                    "name": "B2-risk-jinzhou.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [],
            "score": 0.619195620177432,
            "tsne_position": null,
            "files": [],
            "summary": null
        },
        {
            "segment": {
                "id": "fd9f58cf-64c7-4dc2-a359-5b3cd1c16ef6",
                "position": 7,
                "document_id": "0893e587-6285-405d-b91b-d44c63fa2589",
                "content": "\u8bc4\u5206\u5361\u81ea\u52a8\u901a\u8fc7\u7ebf\u8bbe\u5728 605 \u5206\uff0c\u8fbe\u5230\u8be5\u7ebf\u7684\u7533\u8bf7\u76f4\u63a5\u8fdb\u5165\u653e\u6b3e\u961f\u5217\u3002",
                "sign_content": null,
                "answer": null,
                "word_count": 33,
                "tokens": 67,
                "keywords": [],
                "index_node_id": "ca0a0f22-263c-4555-974b-d0820dd4d148",
                "index_node_hash": "5f01f0cec96f01e11ab9396b0843abe066630cd1d342ea19176d6dd7709e1c24",
                "hit_count": 0,
                "enabled": true,
                "disabled_at": null,
                "disabled_by": null,
                "status": "completed",
                "created_by": "6fcc950b-4861-4d8a-8172-42144f692df1",
                "created_at": 1785799547,
                "indexing_at": 1785799547,
                "completed_at": 1785799549,
                "error": null,
                "stopped_at": null,
                "document": {
                    "id": "0893e587-6285-405d-b91b-d44c63fa2589",
                    "data_source_type": "upload_file",
                    "name": "B4-risk-qihe.md",
                    "doc_type": null,
                    "doc_metadata": null
                }
            },
            "child_chunks": [],
            "score": 0.6096711345271001,
            "tsne_position": null,
            "files": [],
            "summary": null
        }
    ]
}
```



