# frozen_string_literal: true

# Run with the pinned Postal image's Rails runner, never the application DB.
require "json"
require "resolv"

spec = JSON.parse(File.read("/config/workflow.json"))
action = ENV.fetch("POSTAL_WORKFLOW_ACTION", "provision")

if action == "provision"
  admin = User.find_or_initialize_by(email_address: spec.fetch("admin_email"))
  if admin.new_record?
    admin.assign_attributes(first_name: "LLM", last_name: "Lab", admin: true,
                            email_verified_at: Time.now, password: spec.fetch("admin_password"))
    admin.save!
  end
  org = Organization.find_or_create_by!(permalink: "llmlab") do |record|
    record.name = "LLM Lab"
    record.owner = admin
  end
  server = org.servers.find_or_create_by!(permalink: "transactional") do |record|
    record.name = "App confirmations and password resets"
    record.mode = "Development"
    record.raw_message_retention_days = 3
    record.message_retention_days = 14
  end
  domain = server.domains.find_or_initialize_by(name: spec.fetch("sender_domain"))
  if domain.new_record?
    domain.verification_method = "DNS"
    domain.incoming = false
    domain.save!
    # Postal defaults to 1024 bits; use 2048 bits for new sending domains.
    domain.update!(dkim_private_key: OpenSSL::PKey::RSA.new(2048).to_pem)
  end
  credential = server.credentials.find_or_create_by!(type: "SMTP", name: "Django")
else
  server = Organization.find_by!(permalink: "llmlab").servers.find_by!(permalink: "transactional")
  domain = server.domains.find_by!(name: spec.fetch("sender_domain"))
end

hostname = spec.fetch("hostname")
sender = spec.fetch("sender_domain")
ip = spec.fetch("public_ip")
return_path = Postal::Config.dns.return_path_domain
records = [
  { type: "A", name: hostname, value: ip },
  { type: "TXT", name: Postal::Config.dns.spf_include, value: "v=spf1 ip4:#{ip} -all" },
  { type: "A", name: return_path, value: ip },
  { type: "MX", name: return_path, value: "10 #{hostname}" },
  { type: "TXT", name: return_path, value: "v=spf1 ip4:#{ip} -all" },
  { type: "TXT", name: "postal._domainkey.#{return_path}", value: Postal.rp_dkim_dns_record },
  { type: "TXT", name: sender, value: domain.dns_verification_string },
  { type: "TXT", name: sender, value: domain.spf_record },
  { type: "TXT", name: "#{domain.dkim_record_name}.#{sender}", value: domain.dkim_record },
  { type: "CNAME", name: domain.return_path_domain, value: return_path },
  { type: "TXT", name: "_dmarc.#{sender}", value: "v=DMARC1; p=none; adkim=s; aspf=r" }
]
result = { hostname: hostname, sender_domain: sender, public_ip: ip, records: records,
           reverse_dns: { ip: ip, hostname: hostname }, mode: server.mode }

if action == "provision"
  result[:smtp_password] = credential.key
else
  failures = []
  Resolv::DNS.open do |dns|
    dns.timeouts = [2, 3]
    records.each do |record|
      type = Resolv::DNS::Resource::IN.const_get(record[:type])
      answers = dns.getresources(record[:name], type).map do |answer|
        case record[:type]
        when "A" then answer.address.to_s
        when "TXT" then answer.strings.join
        when "MX" then "#{answer.preference} #{answer.exchange}"
        when "CNAME" then answer.name.to_s
        end
      end
      failures << "#{record[:type]} #{record[:name]}" unless answers.include?(record[:value])
    rescue Resolv::ResolvError, Resolv::ResolvTimeout => e
      failures << "#{record[:type]} #{record[:name]}: #{e.class}"
    end
  end
  begin
    actual_ptr = Resolv.getname(ip)
    failures << "PTR #{ip} (currently #{actual_ptr})" unless actual_ptr == hostname
  rescue Resolv::ResolvError
    failures << "PTR #{ip}"
  end
  # Never bypass Postal's ownership verification or sender authentication.
  domain.verify_with_dns unless domain.verified?
  domain.check_dns
  result[:verified] = domain.reload.verified?
  result[:ready_for_delivery] = failures.empty? && domain.verified? && domain.dns_ok?
  result[:pending] = failures
  result[:spf_status] = domain.spf_status
  result[:dkim_status] = domain.dkim_status
  if action == "activate" && result[:ready_for_delivery]
    server.update!(mode: "Live")
    result[:mode] = "Live"
  end
end

puts "POSTAL_WORKFLOW=#{JSON.generate(result)}"
