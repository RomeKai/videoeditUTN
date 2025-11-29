from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    """
    Usuario personalizado para OneCreator.
    Hereda de AbstractUser para tener username, password, email, first_name, etc.
    """
    email = models.EmailField('email address', unique=True)
    
    # --- Datos de Negocio (MVP) ---
    # Saldo de tokens para procesar videos
    tokens_balance = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0.00,
        help_text="Saldo disponible para gastar en procesamiento de IA"
    )
    
    # Opcional: Para saber si es usuario Free, Pro, etc.
    PLAN_CHOICES = [
        ('free', 'Free'),
        ('creator', 'Creator'),
        ('agency', 'Agency'),
    ]
    current_plan = models.CharField(
        max_length=20, 
        choices=PLAN_CHOICES, 
        default='free'
    )

    # Configuración para hacer login con EMAIL en lugar de Username (Opcional pero recomendado)
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username'] # Username sigue siendo requerido internamente

    def __str__(self):
        return self.email