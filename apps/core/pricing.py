class PricingConfig:
    TOKENS_PER_SECOND = 1
    
    PLAN_FEATURES = {
        'free': {
            'allowed_styles': ['minimalist'],
            'max_duration_seconds': 300,
            'max_members': 1,  # <--- NUEVO: Solo tú
        },
        'creator': {
            'allowed_styles': ['minimalist', 'dynamic', 'vlog', 'hormozi'],
            'max_duration_seconds': 1200,
            'max_members': 1,  # <--- NUEVO: Sigue siendo personal
        },
        'agency': {
            'allowed_styles': ['all'],
            'max_duration_seconds': 3600,
            'max_members': 10, # <--- NUEVO: Permite equipo
        }
    }