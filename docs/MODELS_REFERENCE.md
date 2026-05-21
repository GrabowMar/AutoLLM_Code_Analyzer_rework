# Models Reference

> **Summary**: Supported LLM providers, model identifiers, capabilities, and configuration for code generation.
> **Key files**: `src/app/services/openrouter_service.py`, `models_to_test.md`
> **See also**: [Generation Process](GENERATION_PROCESS.md)

Reference documentation for supported AI models and their capabilities.

## Supported Providers

| Provider | Models | Capabilities |
|----------|--------|--------------|
| OpenAI | o1, o3-mini, GPT-4o, GPT-4o-mini | Reasoning, fast generation, multi-modal |
| Anthropic | Claude 3.7 Sonnet, 3.5 Sonnet, 3.5 Haiku | Advanced reasoning, coding specialized |
| Google | Gemini 2.0 Flash, 1.5 Pro | Large context, multi-modal analysis |
| DeepSeek | DeepSeek-R1, DeepSeek-V3 | Reasoning, competitive coding performance |
| Qwen | Qwen2.5-Coder (32B, 7B) | Coding specialized open-weights |
| Meta | Llama 3.3, Llama 3.1 | Strong general-purpose open-weights |

## Model Slug Format

Models are identified by slugs in the format: `{provider}_{model-name}`

Examples:
- `openai_gpt-4`
- `anthropic_claude-3-opus`
- `google_gemini-pro`

### Slug Normalization

The system automatically normalizes model slugs to handle variants:

```python
from app.utils.slug_utils import normalize_model_slug

# These all normalize to the same slug:
normalize_model_slug("openai/gpt-4")      # "openai_gpt-4"
normalize_model_slug("OpenAI_GPT-4")      # "openai_gpt-4"
normalize_model_slug("openai_gpt-4:free") # "openai_gpt-4" (strips :free suffix)
```

The analyzer manager also tries variant lookups (see `_normalize_and_validate_app` in [analyzer/analyzer_manager.py](../analyzer/analyzer_manager.py)).

## Model Capabilities

### Code Generation

All models support generating:
- Flask/Python backends
- React/JavaScript frontends
- Full-stack applications

### Analysis Support

| Model | Static | Dynamic | Performance | AI Review |
|-------|--------|---------|-------------|-----------|
| o1/o3-mini | ✓ | ✓ | ✓ | ✓ |
| Claude 3.x | ✓ | ✓ | ✓ | ✓ |
| Gemini 2.0 | ✓ | ✓ | ✓ | ✓ |
| DeepSeek-R1 | ✓ | ✓ | ✓ | ✓ |
| Llama 3.3 | ✓ | ✓ | ✓ | Limited |

## Generated Application Structure

Each generated app follows this structure:

```
generated/apps/{model_slug}/app{N}/
├── backend/
│   ├── app.py
│   ├── requirements.txt
│   └── ...
├── frontend/
│   ├── src/
│   ├── package.json
│   └── ...
├── .env
├── docker-compose.yml
└── README.md
```

### Application Tracking

Generated apps are tracked in the database (`GeneratedApplication` model) with:
- `model_slug` - Normalized model identifier
- `app_number` - Sequence number
- `provider` - Model provider (e.g., "openai", "anthropic")
- `template_name` - Requirement template used
- `generation_mode` - GUARDED or UNGUARDED
- `container_status` - Current Docker state
- `missing_since` - Timestamp when filesystem directory went missing (7-day grace period before deletion)
- `parent_app_id` - Links to parent if regeneration
- `batch_id` - Groups apps created together
- `is_generation_failed` - Boolean flag for failure
- `error_message` - Raw error from generation service
- `automatic_fixes` - Count of issues healed by `DependencyHealer`
- `metadata_json` - JSON blob containing logs and extra metrics (e.g., `healing_logs`)

> **Note**: If an app's filesystem directory is deleted, it's marked with `missing_since` but not removed from DB for 7 days, allowing recovery.

## Port Allocation

Each app gets unique ports:

| Component | Port Range |
|-----------|------------|
| Backend | 5001+ (dynamically allocated) |
| Frontend | 8001+ (dynamically allocated) |

Port assignments stored in `misc/port_config.json`.

## Model Configuration

### Via API

```bash
curl -X POST http://localhost:5000/api/generation/create \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "model_slug": "openai_gpt-4",
    "template": "crud_todo_list",
    "app_number": 1
  }'
```

### Via Web UI

1. Navigate to **Applications → Generate**
2. Select model from dropdown
3. Choose template
4. Configure options
5. Click **Generate**

## Model Comparison

When analyzing generated apps, consider:

| Metric | Description |
|--------|-------------|
| Code Quality | Linting score, type coverage |
| Security | Vulnerability count by severity |
| Performance | Response time, throughput |
| Compliance | Requirements coverage |

## Templates

The system includes **30 application templates** across 20+ categories in `misc/requirements/`. Example templates:

| Template | Category | Description |
|----------|----------|-------------|
| `crud_todo_list` | CRUD | Task management with soft delete |
| `crud_book_library` | CRUD | Book collection management |
| `auth_user_login` | Auth | User registration and login |
| `ecommerce_cart` | E-commerce | Shopping cart functionality |
| `realtime_chat_room` | Real-time | WebSocket-based messaging |
| `api_weather_display` | API | External weather API integration |

> See [TEMPLATE_SPECIFICATION.md](./TEMPLATE_SPECIFICATION.md) for the complete list of all 30 templates with detailed JSON schema documentation.

## Related

- [Architecture](ARCHITECTURE.md)
- [Background Services](BACKGROUND_SERVICES.md)
- [API Reference](api-reference.md)
- [Analyzer Guide](ANALYZER_GUIDE.md)
- [Development Guide](development-guide.md)
