@{
    HA_LONG_LIVED_TOKEN = @{
        ItemName = 'Home Assistant'
        Sources  = @(
            'notes:HA_LONG_LIVED_TOKEN',
            'notes:long lived token',
            'login:password'
        )
    }

    PFSENSE_PASS = @{
        ItemName = 'Router'
        Sources  = @(
            'login:password',
            'notes:PFSENSE_PASS'
        )
    }

    SECONION_URL = @{
        ItemName = 'Security Onion'
        Sources  = @(
            'field:base_url',
            'notes:SECONION_URL',
            'notes:base url',
            'login:uri'
        )
    }

    SECONION_CLIENT_ID = @{
        ItemName = 'Security Onion'
        Sources  = @(
            'field:client_id',
            'notes:SECONION_CLIENT_ID',
            'notes:client id'
        )
    }

    SECONION_CLIENT_SECRET = @{
        ItemName = 'Security Onion'
        Sources  = @(
            'field:client_secret',
            'notes:SECONION_CLIENT_SECRET',
            'notes:client secret',
            'login:password'
        )
    }
}
