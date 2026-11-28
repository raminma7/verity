# ruff: noqa: E501  (one translation per line reads better than wrapped strings)
"""The system emails' wording in each interface language (en, es, de, fr, it, ar).

Each entry is (subject, body) with {named} placeholders. Bodies use plain text; links are their own
lines so the HTML version can turn them into anchors. A missing language falls back to English.
Translations were written for launch and should be reviewed by native speakers.
"""

from datetime import date, datetime

LOCALES = ("en", "es", "de", "fr", "it", "ar")

MONTHS = {
    "en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec",
    "es": "ene feb mar abr may jun jul ago sep oct nov dic",
    "de": "Jan. Feb. März Apr. Mai Juni Juli Aug. Sept. Okt. Nov. Dez.",
    "fr": "janv. févr. mars avr. mai juin juil. août sept. oct. nov. déc.",
    "it": "gen feb mar apr mag giu lug ago set ott nov dic",
    "ar": "يناير فبراير مارس أبريل مايو يونيو يوليو أغسطس سبتمبر أكتوبر نوفمبر ديسمبر",
}


def lang(locale: str | None) -> str:
    return locale if locale in LOCALES else "en"


def day(value: date | datetime | str | None, locale: str) -> str:
    """A calendar day in the recipient's language, e.g. 5 Mar 2026 / 5. März 2026 / 5 مارس 2026."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    month = MONTHS[lang(locale)].split(" ")[value.month - 1]
    return f"{value.day}. {month} {value.year}" if lang(locale) == "de" else f"{value.day} {month} {value.year}"


def number(n: int, locale: str) -> str:
    s = f"{n:,}"
    sep = {"es": ".", "de": ".", "it": ".", "fr": "\u202f"}.get(lang(locale))
    return s.replace(",", sep) if sep else s


TEXT: dict[str, dict[str, tuple[str, str]]] = {
    "verify": {
        "en": (
            "Verify your email for Verity",
            "Hi {name},\n\nConfirm this is your email address to finish creating your Verity account:\n\n{url}\n\nThe link works once and expires in {hours} hours. If you didn't sign up, ignore this email.",
        ),
        "es": (
            "Verifica tu email para Verity",
            "Hola, {name}:\n\nConfirma que esta es tu dirección de email para terminar de crear tu cuenta de Verity:\n\n{url}\n\nEl enlace funciona una vez y caduca en {hours} horas. Si no te registraste, ignora este email.",
        ),
        "de": (
            "Bestätige deine E-Mail-Adresse für Verity",
            "Hallo {name},\n\nbestätige, dass dies deine E-Mail-Adresse ist, um dein Verity-Konto fertig einzurichten:\n\n{url}\n\nDer Link funktioniert einmal und läuft in {hours} Stunden ab. Wenn du dich nicht registriert hast, ignoriere diese E-Mail.",
        ),
        "fr": (
            "Vérifiez votre e-mail pour Verity",
            "Bonjour {name},\n\nConfirmez qu’il s’agit bien de votre adresse e-mail pour terminer la création de votre compte Verity :\n\n{url}\n\nLe lien fonctionne une fois et expire dans {hours} heures. Si vous ne vous êtes pas inscrit, ignorez cet e-mail.",
        ),
        "it": (
            "Verifica la tua email per Verity",
            "Ciao {name},\n\nconferma che questo è il tuo indirizzo email per completare la creazione del tuo account Verity:\n\n{url}\n\nIl link funziona una volta e scade tra {hours} ore. Se non ti sei registrato, ignora questa email.",
        ),
        "ar": (
            "تحقق من بريدك الإلكتروني في Verity",
            "مرحبًا {name}،\n\nأكّد أن هذا عنوان بريدك الإلكتروني لإكمال إنشاء حسابك في Verity:\n\n{url}\n\nيعمل الرابط مرة واحدة وتنتهي صلاحيته خلال {hours} ساعة. إذا لم تسجّل، فتجاهل هذه الرسالة.",
        ),
    },
    "reset": {
        "en": (
            "Reset your Verity password",
            "Hi {name},\n\nSomeone asked to reset the password for this account. To choose a new one, open:\n\n{url}\n\nThe link works once and expires in {minutes} minutes. If it wasn't you, ignore this email; your password stays the same.",
        ),
        "es": (
            "Restablece tu contraseña de Verity",
            "Hola, {name}:\n\nAlguien ha pedido restablecer la contraseña de esta cuenta. Para elegir una nueva, abre:\n\n{url}\n\nEl enlace funciona una vez y caduca en {minutes} minutos. Si no fuiste tú, ignora este email; tu contraseña no cambia.",
        ),
        "de": (
            "Setze dein Verity-Passwort zurück",
            "Hallo {name},\n\njemand hat das Zurücksetzen des Passworts für dieses Konto angefordert. Um ein neues zu wählen, öffne:\n\n{url}\n\nDer Link funktioniert einmal und läuft in {minutes} Minuten ab. Wenn du das nicht warst, ignoriere diese E-Mail; dein Passwort bleibt unverändert.",
        ),
        "fr": (
            "Réinitialisez votre mot de passe Verity",
            "Bonjour {name},\n\nQuelqu’un a demandé la réinitialisation du mot de passe de ce compte. Pour en choisir un nouveau, ouvrez :\n\n{url}\n\nLe lien fonctionne une fois et expire dans {minutes} minutes. Si ce n’était pas vous, ignorez cet e-mail ; votre mot de passe reste inchangé.",
        ),
        "it": (
            "Reimposta la password di Verity",
            "Ciao {name},\n\nqualcuno ha chiesto di reimpostare la password di questo account. Per sceglierne una nuova, apri:\n\n{url}\n\nIl link funziona una volta e scade tra {minutes} minuti. Se non sei stato tu, ignora questa email; la password resta invariata.",
        ),
        "ar": (
            "أعد تعيين كلمة مرور Verity",
            "مرحبًا {name}،\n\nطلب أحدهم إعادة تعيين كلمة مرور هذا الحساب. لاختيار كلمة مرور جديدة، افتح:\n\n{url}\n\nيعمل الرابط مرة واحدة وتنتهي صلاحيته خلال {minutes} دقيقة. إذا لم تكن أنت، فتجاهل هذه الرسالة؛ ستبقى كلمة مرورك كما هي.",
        ),
    },
    "invite": {
        "en": (
            "{inviter} invited you to {workspace} on Verity",
            "{inviter} invited you to join the {workspace} workspace on Verity as {role}.\n\nAccept the invitation:\n\n{url}\n\nThe invitation expires in {days} days.",
        ),
        "es": (
            "{inviter} te ha invitado a {workspace} en Verity",
            "{inviter} te ha invitado a unirte al espacio de trabajo {workspace} en Verity como {role}.\n\nAcepta la invitación:\n\n{url}\n\nLa invitación caduca en {days} días.",
        ),
        "de": (
            "{inviter} hat dich zu {workspace} auf Verity eingeladen",
            "{inviter} hat dich eingeladen, dem Workspace {workspace} auf Verity als {role} beizutreten.\n\nEinladung annehmen:\n\n{url}\n\nDie Einladung läuft in {days} Tagen ab.",
        ),
        "fr": (
            "{inviter} vous a invité à rejoindre {workspace} sur Verity",
            "{inviter} vous a invité à rejoindre l’espace de travail {workspace} sur Verity en tant que {role}.\n\nAccepter l’invitation :\n\n{url}\n\nL’invitation expire dans {days} jours.",
        ),
        "it": (
            "{inviter} ti ha invitato in {workspace} su Verity",
            "{inviter} ti ha invitato a unirti al workspace {workspace} su Verity come {role}.\n\nAccetta l’invito:\n\n{url}\n\nL’invito scade tra {days} giorni.",
        ),
        "ar": (
            "دعاك {inviter} إلى {workspace} على Verity",
            "دعاك {inviter} للانضمام إلى مساحة العمل {workspace} على Verity بصفة {role}.\n\nاقبل الدعوة:\n\n{url}\n\nتنتهي صلاحية الدعوة خلال {days} يومًا.",
        ),
    },
    "usage": {
        "en": (
            "{workspace} has used {percent}% of its AI tokens",
            "The {workspace} workspace has used {percent}% of this period's AI token allowance. {remaining} weighted tokens remain.\n\nWhen the allowance runs out, AI answers pause. Manual ticketing, email ingestion and forms keep working.\n\nSee usage or upgrade:\n\n{url}",
        ),
        "es": (
            "{workspace} ha usado el {percent}% de sus tokens de IA",
            "El espacio de trabajo {workspace} ha usado el {percent}% de la asignación de tokens de IA de este periodo. Quedan {remaining} tokens ponderados.\n\nCuando se agote la asignación, las respuestas de IA se pausan. Los tickets manuales, la recepción de email y los formularios siguen funcionando.\n\nConsulta el uso o mejora tu plan:\n\n{url}",
        ),
        "de": (
            "{workspace} hat {percent}% seiner KI-Tokens verbraucht",
            "Der Workspace {workspace} hat {percent}% des KI-Token-Kontingents dieses Zeitraums verbraucht. Es bleiben {remaining} gewichtete Tokens.\n\nWenn das Kontingent aufgebraucht ist, pausieren KI-Antworten. Manuelle Tickets, E-Mail-Eingang und Formulare funktionieren weiter.\n\nNutzung ansehen oder upgraden:\n\n{url}",
        ),
        "fr": (
            "{workspace} a utilisé {percent} % de ses jetons IA",
            "L’espace de travail {workspace} a utilisé {percent} % de l’allocation de jetons IA de cette période. Il reste {remaining} jetons pondérés.\n\nQuand l’allocation est épuisée, les réponses IA sont mises en pause. Les tickets manuels, la réception des e-mails et les formulaires continuent de fonctionner.\n\nVoir l’utilisation ou passer au forfait supérieur :\n\n{url}",
        ),
        "it": (
            "{workspace} ha usato il {percent}% dei suoi token IA",
            "Il workspace {workspace} ha usato il {percent}% dell’assegnazione di token IA di questo periodo. Restano {remaining} token ponderati.\n\nQuando l’assegnazione finisce, le risposte IA vanno in pausa. Ticket manuali, ricezione email e moduli continuano a funzionare.\n\nVedi l’utilizzo o passa a un piano superiore:\n\n{url}",
        ),
        "ar": (
            "استخدمت {workspace} نسبة {percent}% من رموز الذكاء الاصطناعي",
            "استخدمت مساحة العمل {workspace} نسبة {percent}% من حصة رموز الذكاء الاصطناعي لهذه الفترة. يتبقى {remaining} رمزًا موزونًا.\n\nعند نفاد الحصة تتوقف إجابات الذكاء الاصطناعي. تظل التذاكر اليدوية واستلام البريد والنماذج تعمل.\n\nاطّلع على الاستخدام أو رقِّ خطتك:\n\n{url}",
        ),
    },
    "receipt": {
        "en": (
            "Receipt: Verity {plan}",
            "Payment received for the {workspace} workspace.\n\nPlan: {plan}\nAmount: {amount}\nRenews on: {period_end}\n\nBilling history:\n\n{url}",
        ),
        "es": (
            "Recibo: Verity {plan}",
            "Pago recibido para el espacio de trabajo {workspace}.\n\nPlan: {plan}\nImporte: {amount}\nSe renueva el: {period_end}\n\nHistorial de facturación:\n\n{url}",
        ),
        "de": (
            "Beleg: Verity {plan}",
            "Zahlung für den Workspace {workspace} erhalten.\n\nPlan: {plan}\nBetrag: {amount}\nVerlängert sich am: {period_end}\n\nAbrechnungsverlauf:\n\n{url}",
        ),
        "fr": (
            "Reçu : Verity {plan}",
            "Paiement reçu pour l’espace de travail {workspace}.\n\nForfait : {plan}\nMontant : {amount}\nRenouvellement le : {period_end}\n\nHistorique de facturation :\n\n{url}",
        ),
        "it": (
            "Ricevuta: Verity {plan}",
            "Pagamento ricevuto per il workspace {workspace}.\n\nPiano: {plan}\nImporto: {amount}\nSi rinnova il: {period_end}\n\nStorico fatturazione:\n\n{url}",
        ),
        "ar": (
            "إيصال: Verity {plan}",
            "تم استلام الدفعة لمساحة العمل {workspace}.\n\nالخطة: {plan}\nالمبلغ: {amount}\nتتجدد في: {period_end}\n\nسجل الفوترة:\n\n{url}",
        ),
    },
    "failed": {
        "en": (
            "Payment failed: Verity {plan}",
            "The {amount} renewal of {plan} for the {workspace} workspace didn't go through{reason}.\n\n{retry} Until it's paid, AI answers are paused. Tickets, email and forms keep working until {grace_end}; after that the subscription ends.\n\nUpdate the payment method or retry now:\n\n{url}",
        ),
        "es": (
            "Pago fallido: Verity {plan}",
            "La renovación de {plan} por {amount} para el espacio de trabajo {workspace} no se completó{reason}.\n\n{retry} Hasta que se pague, las respuestas de IA están en pausa. Los tickets, el email y los formularios siguen funcionando hasta el {grace_end}; después, la suscripción termina.\n\nActualiza el método de pago o reintenta ahora:\n\n{url}",
        ),
        "de": (
            "Zahlung fehlgeschlagen: Verity {plan}",
            "Die Verlängerung von {plan} über {amount} für den Workspace {workspace} ist nicht durchgegangen{reason}.\n\n{retry} Bis zur Zahlung sind KI-Antworten pausiert. Tickets, E-Mail und Formulare funktionieren bis zum {grace_end}; danach endet das Abo.\n\nZahlungsmethode aktualisieren oder jetzt erneut versuchen:\n\n{url}",
        ),
        "fr": (
            "Échec du paiement : Verity {plan}",
            "Le renouvellement de {plan} d’un montant de {amount} pour l’espace de travail {workspace} n’a pas abouti{reason}.\n\n{retry} Tant qu’il n’est pas réglé, les réponses IA sont en pause. Les tickets, les e-mails et les formulaires fonctionnent jusqu’au {grace_end} ; ensuite, l’abonnement prend fin.\n\nMettez à jour le moyen de paiement ou réessayez maintenant :\n\n{url}",
        ),
        "it": (
            "Pagamento non riuscito: Verity {plan}",
            "Il rinnovo di {plan} da {amount} per il workspace {workspace} non è andato a buon fine{reason}.\n\n{retry} Finché non viene pagato, le risposte IA sono in pausa. Ticket, email e moduli funzionano fino al {grace_end}; dopo, l’abbonamento termina.\n\nAggiorna il metodo di pagamento o riprova ora:\n\n{url}",
        ),
        "ar": (
            "فشل الدفع: Verity {plan}",
            "لم يتم تجديد {plan} بمبلغ {amount} لمساحة العمل {workspace}{reason}.\n\n{retry} إلى أن يتم الدفع تتوقف إجابات الذكاء الاصطناعي. تظل التذاكر والبريد والنماذج تعمل حتى {grace_end}؛ بعدها ينتهي الاشتراك.\n\nحدّث وسيلة الدفع أو أعد المحاولة الآن:\n\n{url}",
        ),
    },
    "retry_on": {
        "en": ("", "We'll try again on {next_retry}."),
        "es": ("", "Volveremos a intentarlo el {next_retry}."),
        "de": ("", "Wir versuchen es am {next_retry} erneut."),
        "fr": ("", "Nous réessaierons le {next_retry}."),
        "it": ("", "Riproveremo il {next_retry}."),
        "ar": ("", "سنحاول مجددًا في {next_retry}."),
    },
    "retry_none": {
        "en": ("", "There are no automatic retries left."),
        "es": ("", "No quedan reintentos automáticos."),
        "de": ("", "Es gibt keine automatischen Versuche mehr."),
        "fr": ("", "Il ne reste plus de nouvel essai automatique."),
        "it": ("", "Non restano tentativi automatici."),
        "ar": ("", "لم تتبقَّ محاولات تلقائية."),
    },
    "recovered": {
        "en": (
            "Payment received: Verity {plan}",
            "The renewal of {plan} for the {workspace} workspace has been paid. AI answers are back on.\n\n{url}",
        ),
        "es": (
            "Pago recibido: Verity {plan}",
            "Se ha pagado la renovación de {plan} para el espacio de trabajo {workspace}. Las respuestas de IA vuelven a estar activas.\n\n{url}",
        ),
        "de": (
            "Zahlung erhalten: Verity {plan}",
            "Die Verlängerung von {plan} für den Workspace {workspace} wurde bezahlt. KI-Antworten sind wieder eingeschaltet.\n\n{url}",
        ),
        "fr": (
            "Paiement reçu : Verity {plan}",
            "Le renouvellement de {plan} pour l’espace de travail {workspace} a été payé. Les réponses IA sont de nouveau actives.\n\n{url}",
        ),
        "it": (
            "Pagamento ricevuto: Verity {plan}",
            "Il rinnovo di {plan} per il workspace {workspace} è stato pagato. Le risposte IA sono di nuovo attive.\n\n{url}",
        ),
        "ar": (
            "تم استلام الدفعة: Verity {plan}",
            "تم دفع تجديد {plan} لمساحة العمل {workspace}. عادت إجابات الذكاء الاصطناعي إلى العمل.\n\n{url}",
        ),
    },
    "ended": {
        "en": (
            "Your Verity {plan} subscription has ended",
            "The {plan} subscription of the {workspace} workspace ended because its renewal couldn't be charged. Your data is kept. Choose a plan to start again:\n\n{url}",
        ),
        "es": (
            "Tu suscripción a Verity {plan} ha terminado",
            "La suscripción {plan} del espacio de trabajo {workspace} terminó porque no se pudo cobrar la renovación. Tus datos se conservan. Elige un plan para empezar de nuevo:\n\n{url}",
        ),
        "de": (
            "Dein Verity-Abo {plan} ist beendet",
            "Das Abo {plan} des Workspace {workspace} ist beendet, weil die Verlängerung nicht abgebucht werden konnte. Deine Daten bleiben erhalten. Wähle einen Plan, um neu zu starten:\n\n{url}",
        ),
        "fr": (
            "Votre abonnement Verity {plan} a pris fin",
            "L’abonnement {plan} de l’espace de travail {workspace} a pris fin car son renouvellement n’a pas pu être débité. Vos données sont conservées. Choisissez un forfait pour recommencer :\n\n{url}",
        ),
        "it": (
            "Il tuo abbonamento Verity {plan} è terminato",
            "L’abbonamento {plan} del workspace {workspace} è terminato perché non è stato possibile addebitare il rinnovo. I tuoi dati sono conservati. Scegli un piano per ricominciare:\n\n{url}",
        ),
        "ar": (
            "انتهى اشتراكك في Verity {plan}",
            "انتهى اشتراك {plan} لمساحة العمل {workspace} لأنه تعذّر تحصيل التجديد. بياناتك محفوظة. اختر خطة للبدء من جديد:\n\n{url}",
        ),
    },
    "package": {
        "en": (
            "Receipt: {package}",
            "Payment received for the {workspace} workspace.\n\nToken package: {package}\nTokens: {tokens}\nAmount: {amount}\n\nThese tokens are used after the plan's allowance, carry over from period to period and expire 12 months after purchase.\n\n{url}",
        ),
        "es": (
            "Recibo: {package}",
            "Pago recibido para el espacio de trabajo {workspace}.\n\nPaquete de tokens: {package}\nTokens: {tokens}\nImporte: {amount}\n\nEstos tokens se usan después de la asignación del plan, se acumulan de un periodo a otro y caducan 12 meses después de la compra.\n\n{url}",
        ),
        "de": (
            "Beleg: {package}",
            "Zahlung für den Workspace {workspace} erhalten.\n\nToken-Paket: {package}\nTokens: {tokens}\nBetrag: {amount}\n\nDiese Tokens werden nach dem Plan-Kontingent genutzt, in den nächsten Zeitraum übernommen und verfallen 12 Monate nach dem Kauf.\n\n{url}",
        ),
        "fr": (
            "Reçu : {package}",
            "Paiement reçu pour l’espace de travail {workspace}.\n\nPack de jetons : {package}\nJetons : {tokens}\nMontant : {amount}\n\nCes jetons sont utilisés après l’allocation du forfait, sont reportés d’une période à l’autre et expirent 12 mois après l’achat.\n\n{url}",
        ),
        "it": (
            "Ricevuta: {package}",
            "Pagamento ricevuto per il workspace {workspace}.\n\nPacchetto di token: {package}\nToken: {tokens}\nImporto: {amount}\n\nQuesti token si usano dopo l’assegnazione del piano, passano da un periodo all’altro e scadono 12 mesi dopo l’acquisto.\n\n{url}",
        ),
        "ar": (
            "إيصال: {package}",
            "تم استلام الدفعة لمساحة العمل {workspace}.\n\nحزمة الرموز: {package}\nالرموز: {tokens}\nالمبلغ: {amount}\n\nتُستخدم هذه الرموز بعد حصة الخطة، وتُرحَّل من فترة إلى أخرى، وتنتهي بعد 12 شهرًا من الشراء.\n\n{url}",
        ),
    },
    "export": {
        "en": (
            "Your Verity data export is ready",
            "The export of {what} is ready. Download it while signed in to Verity:\n\n{url}\n\nThe link works once and expires on {expires}.",
        ),
        "es": (
            "Tu exportación de datos de Verity está lista",
            "La exportación de {what} está lista. Descárgala con la sesión iniciada en Verity:\n\n{url}\n\nEl enlace funciona una vez y caduca el {expires}.",
        ),
        "de": (
            "Dein Verity-Datenexport ist fertig",
            "Der Export {what} ist fertig. Lade ihn herunter, während du bei Verity angemeldet bist:\n\n{url}\n\nDer Link funktioniert einmal und läuft am {expires} ab.",
        ),
        "fr": (
            "Votre export de données Verity est prêt",
            "L’export {what} est prêt. Téléchargez-le en étant connecté à Verity :\n\n{url}\n\nLe lien fonctionne une fois et expire le {expires}.",
        ),
        "it": (
            "La tua esportazione di dati Verity è pronta",
            "L’esportazione {what} è pronta. Scaricala dopo aver effettuato l’accesso a Verity:\n\n{url}\n\nIl link funziona una volta e scade il {expires}.",
        ),
        "ar": (
            "تصدير بياناتك من Verity جاهز",
            "تصدير {what} جاهز. نزّله بعد تسجيل الدخول إلى Verity:\n\n{url}\n\nيعمل الرابط مرة واحدة وتنتهي صلاحيته في {expires}.",
        ),
    },
    "export_workspace": {
        "en": ("", "the {workspace} workspace's data"),
        "es": ("", "los datos del espacio de trabajo {workspace}"),
        "de": ("", "der Daten des Workspace {workspace}"),
        "fr": ("", "des données de l’espace de travail {workspace}"),
        "it": ("", "dei dati del workspace {workspace}"),
        "ar": ("", "بيانات مساحة العمل {workspace}"),
    },
    "export_personal": {
        "en": ("", "your personal data"),
        "es": ("", "tus datos personales"),
        "de": ("", "deiner persönlichen Daten"),
        "fr": ("", "de vos données personnelles"),
        "it": ("", "dei tuoi dati personali"),
        "ar": ("", "بياناتك الشخصية"),
    },
    "ws_delete": {
        "en": (
            "The {workspace} workspace will be deleted",
            "The {workspace} workspace has been closed and will be permanently erased on {erase_on}, with its tickets, conversations, email, knowledge base and files.\n\n{undo}",
        ),
        "es": (
            "El espacio de trabajo {workspace} se eliminará",
            "El espacio de trabajo {workspace} se ha cerrado y se borrará de forma permanente el {erase_on}, con sus tickets, conversaciones, emails, base de conocimiento y archivos.\n\n{undo}",
        ),
        "de": (
            "Der Workspace {workspace} wird gelöscht",
            "Der Workspace {workspace} wurde geschlossen und wird am {erase_on} endgültig gelöscht, mit seinen Tickets, Unterhaltungen, E-Mails, der Wissensdatenbank und den Dateien.\n\n{undo}",
        ),
        "fr": (
            "L’espace de travail {workspace} va être supprimé",
            "L’espace de travail {workspace} a été fermé et sera définitivement effacé le {erase_on}, avec ses tickets, conversations, e-mails, base de connaissances et fichiers.\n\n{undo}",
        ),
        "it": (
            "Il workspace {workspace} verrà eliminato",
            "Il workspace {workspace} è stato chiuso e verrà cancellato definitivamente il {erase_on}, con ticket, conversazioni, email, knowledge base e file.\n\n{undo}",
        ),
        "ar": (
            "ستُحذف مساحة العمل {workspace}",
            "أُغلقت مساحة العمل {workspace} وستُمحى نهائيًا في {erase_on}، مع تذاكرها ومحادثاتها وبريدها وقاعدة معرفتها وملفاتها.\n\n{undo}",
        ),
    },
    "ws_undo_owner": {
        "en": ("", "You can undo this until {erase_on} under Workspaces:\n\n{url}"),
        "es": ("", "Puedes deshacerlo hasta el {erase_on} en Espacios de trabajo:\n\n{url}"),
        "de": ("", "Du kannst das bis zum {erase_on} unter Workspaces rückgängig machen:\n\n{url}"),
        "fr": ("", "Vous pouvez annuler jusqu’au {erase_on} depuis Espaces de travail :\n\n{url}"),
        "it": ("", "Puoi annullare fino al {erase_on} in Workspace:\n\n{url}"),
        "ar": ("", "يمكنك التراجع حتى {erase_on} من صفحة مساحات العمل:\n\n{url}"),
    },
    "ws_undo_member": {
        "en": ("", "The workspace owner can undo this until then."),
        "es": ("", "El propietario del espacio de trabajo puede deshacerlo hasta entonces."),
        "de": ("", "Der Inhaber des Workspace kann das bis dahin rückgängig machen."),
        "fr": ("", "Le propriétaire de l’espace de travail peut annuler d’ici là."),
        "it": ("", "Il proprietario del workspace può annullare fino ad allora."),
        "ar": ("", "يمكن لمالك مساحة العمل التراجع حتى ذلك الحين."),
    },
    "account_delete": {
        "en": (
            "Your Verity account will be deleted",
            "You asked to delete your Verity account. You've been signed out everywhere, and your account will be permanently anonymized on {erase_on}.\n\nChanged your mind? Sign in before then to keep it:\n\n{url}",
        ),
        "es": (
            "Tu cuenta de Verity se eliminará",
            "Has pedido eliminar tu cuenta de Verity. Se ha cerrado tu sesión en todas partes y tu cuenta se anonimizará de forma permanente el {erase_on}.\n\n¿Has cambiado de opinión? Inicia sesión antes de esa fecha para conservarla:\n\n{url}",
        ),
        "de": (
            "Dein Verity-Konto wird gelöscht",
            "Du hast die Löschung deines Verity-Kontos beantragt. Du wurdest überall abgemeldet, und dein Konto wird am {erase_on} endgültig anonymisiert.\n\nDoch anders entschieden? Melde dich vorher an, um es zu behalten:\n\n{url}",
        ),
        "fr": (
            "Votre compte Verity va être supprimé",
            "Vous avez demandé la suppression de votre compte Verity. Vous avez été déconnecté partout, et votre compte sera définitivement anonymisé le {erase_on}.\n\nVous avez changé d’avis ? Connectez-vous avant cette date pour le conserver :\n\n{url}",
        ),
        "it": (
            "Il tuo account Verity verrà eliminato",
            "Hai chiesto di eliminare il tuo account Verity. Sei stato disconnesso ovunque e il tuo account verrà anonimizzato definitivamente il {erase_on}.\n\nHai cambiato idea? Accedi prima di quella data per mantenerlo:\n\n{url}",
        ),
        "ar": (
            "سيُحذف حسابك في Verity",
            "طلبت حذف حسابك في Verity. تم تسجيل خروجك من كل مكان، وسيُجهَّل حسابك نهائيًا في {erase_on}.\n\nغيّرت رأيك؟ سجّل الدخول قبل ذلك للاحتفاظ به:\n\n{url}",
        ),
    },
    "escalation": {
        "en": (
            "A customer is waiting for a reply in {workspace}",
            "A conversation with {chatbot} was handed to your team. The assistant stays quiet until someone resolves it.\n\nLast message from the customer:\n“{snippet}”\n\nReply here:\n\n{url}",
        ),
        "es": (
            "Un cliente espera respuesta en {workspace}",
            "Una conversación con {chatbot} se ha pasado a tu equipo. El asistente no interviene hasta que alguien la resuelva.\n\nÚltimo mensaje del cliente:\n“{snippet}”\n\nResponde aquí:\n\n{url}",
        ),
        "de": (
            "Ein Kunde wartet in {workspace} auf eine Antwort",
            "Eine Unterhaltung mit {chatbot} wurde an dein Team übergeben. Der Assistent schweigt, bis jemand sie löst.\n\nLetzte Nachricht des Kunden:\n„{snippet}“\n\nHier antworten:\n\n{url}",
        ),
        "fr": (
            "Un client attend une réponse dans {workspace}",
            "Une conversation avec {chatbot} a été transmise à votre équipe. L’assistant reste silencieux jusqu’à ce que quelqu’un la résolve.\n\nDernier message du client :\n« {snippet} »\n\nRépondre ici :\n\n{url}",
        ),
        "it": (
            "Un cliente attende una risposta in {workspace}",
            "Una conversazione con {chatbot} è stata passata al tuo team. L’assistente resta in silenzio finché qualcuno non la risolve.\n\nUltimo messaggio del cliente:\n“{snippet}”\n\nRispondi qui:\n\n{url}",
        ),
        "ar": (
            "عميل ينتظر ردًا في {workspace}",
            "أُحيلت محادثة مع {chatbot} إلى فريقك. يبقى المساعد صامتًا حتى يحلها أحد.\n\nآخر رسالة من العميل:\n«{snippet}»\n\nردّ من هنا:\n\n{url}",
        ),
    },
    "assistant": {
        "en": ("", "the assistant"),
        "es": ("", "el asistente"),
        "de": ("", "dem Assistenten"),
        "fr": ("", "l’assistant"),
        "it": ("", "l’assistente"),
        "ar": ("", "المساعد"),
    },
    "ticket_created": {
        "en": ("New ticket #{number} in {workspace}", "A customer opened ticket #{number}:\n\n“{subject}”\n\nOpen it here:\n\n{url}"),
        "es": (
            "Nuevo ticket #{number} en {workspace}",
            "Un cliente ha abierto el ticket #{number}:\n\n“{subject}”\n\nÁbrelo aquí:\n\n{url}",
        ),
        "de": ("Neues Ticket #{number} in {workspace}", "Ein Kunde hat Ticket #{number} eröffnet:\n\n„{subject}“\n\nHier öffnen:\n\n{url}"),
        "fr": (
            "Nouveau ticket #{number} dans {workspace}",
            "Un client a ouvert le ticket #{number} :\n\n« {subject} »\n\nL’ouvrir ici :\n\n{url}",
        ),
        "it": ("Nuovo ticket #{number} in {workspace}", "Un cliente ha aperto il ticket #{number}:\n\n“{subject}”\n\nAprilo qui:\n\n{url}"),
        "ar": ("تذكرة جديدة #{number} في {workspace}", "فتح عميل التذكرة #{number}:\n\n«{subject}»\n\nافتحها من هنا:\n\n{url}"),
    },
}


def render(key: str, locale: str | None, **values: object) -> tuple[str, str]:
    """(subject, body) for an email in the recipient's language."""
    subject, body = TEXT[key].get(lang(locale)) or TEXT[key]["en"]
    return subject.format(**values), body.format(**values)


def phrase(key: str, locale: str | None, **values: object) -> str:
    return render(key, locale, **values)[1]
