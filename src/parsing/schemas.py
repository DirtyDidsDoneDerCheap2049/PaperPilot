from pydantic import AliasChoices, BaseModel, Field, field_validator


def _coerce_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return "" if value.strip().lower() in {"无", "none", "null", "n/a"} else value.strip()
    return str(value).strip()


def _coerce_string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        result = []
        for item in value:
            text = _coerce_string(item)
            if text:
                result.append(text)
        return result
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"无", "none", "null", "n/a"}:
            return []
        # LLMs sometimes return newline/comma separated strings instead of arrays.
        separators = ["\n", ";", "；", "，", ","]
        parts = [text]
        for sep in separators:
            if sep in text:
                parts = [p.strip() for p in text.replace("；", ";").replace("，", ",").replace("\n", ",").replace(";", ",").split(",")]
                break
        return [p for p in parts if p]
    text = _coerce_string(value)
    return [text] if text else []


class DirectionParseResult(BaseModel):
    research_question: str = ""
    research_domain: str = ""
    target_task: str = ""
    method_component: str = Field(default="", validation_alias=AliasChoices("method_component", "stereo_matching_stage"))
    method_category: str = ""
    method_subcategory: str = ""
    implementation_details: list[str] = Field(default_factory=list)
    claims_to_verify: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    matrix_axes_hint: list[str] = Field(default_factory=list)

    @field_validator("research_question", "research_domain", "target_task", "method_component", "method_category", "method_subcategory", mode="before")
    @classmethod
    def normalize_string_fields(cls, value):
        return _coerce_string(value)

    @field_validator("implementation_details", "claims_to_verify", "search_queries", "matrix_axes_hint", mode="before")
    @classmethod
    def normalize_list_fields(cls, value):
        return _coerce_string_list(value)


class InnovationProfileSchema(BaseModel):
    paper_id: str = ""
    is_relevant: bool = True
    has_fulltext: bool = False
    research_domain: str | None = None
    task_or_problem: str | None = None
    method_summary: str | None = None
    method_component: str | None = Field(default=None, validation_alias=AliasChoices("method_component", "stereo_matching_stage"))
    method_category: str | None = None
    method_subcategory: str | None = None
    innovation_detail: str = ""
    baseline_method: str | None = None
    replaces_component: str | None = None
    key_techniques: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    performance: dict = Field(default_factory=dict)
    ablation_insights: str = ""
    limitations: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator('limitations', 'key_techniques', 'datasets', mode='before')
    @classmethod
    def normalize_text_lists(cls, value):
        if value is None:return []
        if isinstance(value,str):return [value.strip()] if value.strip() else []
        if isinstance(value,list) and all(isinstance(item,str) for item in value):
            return [item.strip() for item in value if item.strip()]
        raise ValueError('Expected text or a list of text')

    @field_validator('research_domain', 'task_or_problem', 'method_summary',
                     'method_component', 'method_category', 'method_subcategory',
                     'innovation_detail', 'baseline_method', 'replaces_component',
                     'ablation_insights', mode='before')
    @classmethod
    def normalize_descriptive_text(cls, value):
        # Equivalent prose shapes are accepted; evidence objects remain strict.
        if value is None:
            return ''
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return '\n'.join(item.strip() for item in value if item.strip())
        if not isinstance(value, str):
            raise ValueError('Descriptive fields must be text or a list of text')
        return value.strip()


class GapAnalysisResult(BaseModel):
    gaps: list[dict] = Field(default_factory=list)
    matrix: dict = Field(default_factory=dict)
