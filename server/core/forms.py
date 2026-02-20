from django import forms


class SupabaseSettingsForm(forms.Form):
    supabase_url = forms.URLField(label="SUPABASE_URL", required=True)
    supabase_anon_key = forms.CharField(label="SUPABASE_ANON_KEY", required=True)
    supabase_service_role_key = forms.CharField(
        label="SUPABASE_SERVICE_ROLE_KEY",
        required=False,
        widget=forms.PasswordInput(render_value=False),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"
        self.fields["supabase_service_role_key"].help_text = "Оставьте пустым, чтобы не менять ключ."


class AmoCRMSettingsForm(forms.Form):
    domain = forms.CharField(label="domain", required=True)
    access_token = forms.CharField(
        label="access_token", required=False, widget=forms.PasswordInput(render_value=False)
    )
    client_id = forms.CharField(label="client_id", required=False)
    client_secret = forms.CharField(
        label="client_secret", required=False, widget=forms.PasswordInput(render_value=False)
    )
    refresh_token = forms.CharField(
        label="refresh_token", required=False, widget=forms.PasswordInput(render_value=False)
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"
        self.fields["access_token"].help_text = "Нужен для проверки (read-only)."
        self.fields["refresh_token"].help_text = "Оставьте пустым, чтобы не менять токен."


class RadistSettingsForm(forms.Form):
    api_base_url = forms.URLField(label="API base URL", required=False)
    api_key = forms.CharField(label="API key", required=True, widget=forms.PasswordInput(render_value=False))
    company_id = forms.IntegerField(label="Company ID", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"


class AISettingsForm(forms.Form):
    provider = forms.CharField(label="Provider", required=True)
    model = forms.CharField(label="Model", required=False)
    api_key = forms.CharField(label="API key", required=True, widget=forms.PasswordInput(render_value=False))
    prompt = forms.CharField(label="Prompt", required=False, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"


class TelegramSettingsForm(forms.Form):
    bot_token = forms.CharField(label="Bot token", required=True, widget=forms.PasswordInput(render_value=False))
    chat_id = forms.CharField(label="chat_id", required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"


class UserProfileForm(forms.Form):
    email = forms.EmailField(label="Email", required=True)
    phone = forms.CharField(label="Телефон", required=False, max_length=32)
    timezone = forms.CharField(label="Часовой пояс", required=True, max_length=64)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"
