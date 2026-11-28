# Stripe Checkout para compras de tokens

## Configuración del Dashboard

1. Abrí un sandbox en Stripe. Creá un producto de pago único llamado «OneCreator tokens» y copiá su ID de producto en `STRIPE_TOKEN_PRODUCT_ID`. Si dejás esa variable vacía, Checkout recibe el nombre del producto en cada sesión.
2. El catálogo de OneCreator calcula los precios de los paquetes y de las cantidades libres en USD a partir de `TOKEN_PRICE_USD`. La API crea el precio unitario de cada sesión mediante `line_items.price_data`. Si el Dashboard pide un precio inicial para guardar el producto, elegí pago único en USD. La integración usa el ID del producto y el importe calculado por OneCreator, por lo que cambiar ese precio en el Dashboard no cambia lo que cobra la app.
3. Configurá `PAYMENTS_PROVIDER=stripe` y `STRIPE_SECRET_KEY` en el backend y el worker. En el Dashboard agregá un destino de eventos de la cuenta con la URL HTTPS `/api/v1/payments/webhook/` y seleccioná los eventos completos `checkout.session.completed` y `checkout.session.expired`. Guardá el secreto de firma de ese destino en `STRIPE_WEBHOOK_SECRET`. Usá secretos separados para sandbox y producción. En producción configurá también el origen autorizado del frontend mediante `FRONTEND_URL` y, si corresponde, `CORS_ALLOWED_ORIGINS`.
4. El checkout admite tarjeta y opera en USD. El vencimiento se calcula desde el inicio de la llamada de creación. Usá el TTL de 24 horas por defecto o un valor entre 30 minutos más el timeout del proveedor y 5 segundos, y 24 horas. El webhook sigue disponible durante mantenimiento. Si desactivás compras nuevas, conservá ambos secretos hasta liquidar las sesiones que sigan abiertas.

## Prueba manual en sandbox

En un entorno de prueba que ya esté funcionando, autenticá Stripe CLI en el sandbox y abrí el reenviador. Reemplazá el host y el puerto si tu API de prueba usa otros.

```sh
stripe listen --events checkout.session.completed,checkout.session.expired --forward-to http://localhost:8000/api/v1/payments/webhook/
```

Para esa prueba, configurá en el backend el secreto que imprime `stripe listen`. Con un workspace de prueba, abrí un Checkout desde la pantalla Comprar tokens o desde `POST /api/v1/payments/checkout/`. Completalo con una tarjeta de prueba de Stripe y verificá que el estado pase a `approved`, que la billetera aumente por la cantidad comprada y que exista un único movimiento `DEPOSIT`. Con un destino de sandbox registrado, podés reenviar el mismo evento de esa sesión desde el Dashboard y comprobar que el saldo y el movimiento no cambian. Los eventos genéricos de `stripe trigger` sirven para verificar la entrega y la firma, y se ignoran al acreditar porque no corresponden a una compra de OneCreator.

Si Checkout vence mientras llega una confirmación, el barrido consulta el estado de Stripe antes de rechazar el pago. Para casos que requieren conciliación manual, buscá `payment.late_paid`, `payments.stripe_unmatched_paid` o `payments.stripe_session_mismatch` en los logs. El evento firmado de una compra cuyo workspace ya se borró conserva su identificador en el log de conciliación. El de un pago que sigue en la base queda asociado a su Session ID. Revisá esos casos en Stripe antes de aplicar una devolución.

- [Referencia de Checkout Sessions](https://docs.stripe.com/api/checkout/sessions/create)
- [Firma de webhooks](https://docs.stripe.com/webhooks/signature)
- [Stripe CLI](https://docs.stripe.com/stripe-cli/use-cli)
- [Tarjetas y pruebas en sandbox](https://docs.stripe.com/testing)
