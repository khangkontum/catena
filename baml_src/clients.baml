client<llm> Gateway {
  provider "openai-responses"
  options {
    base_url env.LLM_GATEWAY_BASE_URL
    api_key env.LLM_GATEWAY_API_KEY
    model env.LLM_MODEL
    temperature 0
  }
}
