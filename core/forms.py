from django import forms

from .models import AGV


class AGVForm(forms.ModelForm):
    class Meta:
        model = AGV
        fields = [
            "name",
            "description",
            "vpn_ip",
            "hostname",
            "enabled",
            "probe_method",
            "probe_port",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "vpn_ip": forms.TextInput(attrs={"class": "form-control",
                                              "placeholder": "10.0.0.42"}),
            "hostname": forms.TextInput(attrs={"class": "form-control"}),
            "probe_method": forms.Select(attrs={"class": "form-select"}),
            "probe_port": forms.NumberInput(attrs={"class": "form-control",
                                                    "min": 1, "max": 65535}),
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get("probe_method")
        port = cleaned.get("probe_port")
        if method in ("tcp", "http") and not port:
            self.add_error("probe_port",
                            "Port is required for TCP / HTTP probes.")
        return cleaned
